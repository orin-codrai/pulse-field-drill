import hashlib
import hmac
import json
import os
import time
from collections.abc import AsyncIterator
from urllib.parse import urlencode

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

BOT_TOKEN = "12345:test-bot-token"

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://pulse:devpass@localhost:5432/pulse_test",
)


def sign_init_data(
    user: dict,
    bot_token: str = BOT_TOKEN,
    auth_date: int | None = None,
    extra: dict | None = None,
) -> str:
    """Form a URL-encoded initData string with a valid HMAC, matching Telegram's algorithm.

    Used by tests to produce known-good and (via mutation) known-bad inputs.
    """
    if auth_date is None:
        auth_date = int(time.time())
    fields: dict[str, str] = {
        "auth_date": str(auth_date),
        "user": json.dumps(user, separators=(",", ":")),
    }
    if extra:
        fields.update({k: str(v) for k, v in extra.items()})

    data_check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    h = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    return urlencode({**fields, "hash": h})


@pytest.fixture
def bot_token() -> str:
    return BOT_TOKEN


@pytest.fixture
def valid_user() -> dict:
    return {
        "id": 12345,
        "first_name": "Orrin",
        "last_name": "Test",
        "username": "orrin_test",
        "language_code": "en",
        "is_premium": True,
    }


@pytest.fixture
def valid_init_data(valid_user: dict) -> str:
    return sign_init_data(valid_user)


# ─── Async DB fixtures ────────────────────────────────────────────────────────
#
# Стратегия:
# - session-scope `test_engine`: создаёт чистую БД `pulse_test`, накатывает
#   схему через `Base.metadata.create_all`, сидит 18 системных категорий.
# - function-scope `db_session`: TRUNCATE всех «пользовательских» таблиц
#   перед каждым тестом (системные категории сохраняются). Выдаёт свежий
#   AsyncSession. Тесты получают изоляцию без SAVEPOINT-magic.
#
# `@pytest.mark.no_rollback` — для классов TestMigrations/TestConcurrency
# которые делают свой setup; этот маркер игнорирует function-scope truncate.


@pytest_asyncio.fixture(scope="session")
async def test_engine() -> AsyncIterator:
    # Создаём (или пересоздаём) test database из admin connection к postgres-БД.
    admin_url = TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres"
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text("DROP DATABASE IF EXISTS pulse_test"))
        await conn.execute(text("CREATE DATABASE pulse_test"))
    await admin.dispose()

    engine = create_async_engine(TEST_DATABASE_URL)

    # Накатываем схему. Это не alembic — но финальный state у Base.metadata
    # и alembic head синхронизирован (alembic check → No new operations),
    # поэтому create_all даёт ту же структуру.
    from app.db.base import Base
    import app.models  # noqa: F401 — регистрирует все таблицы

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Expression indexes (COALESCE, DESC) не создаются metadata.create_all —
        # они только в alembic 0001. Воспроизводим вручную:
        await conn.execute(
            text(
                "CREATE UNIQUE INDEX categories_user_name_uq "
                "ON categories (COALESCE(user_id, 0), name) "
                "WHERE archived_at IS NULL"
            )
        )
        await conn.execute(text("DROP INDEX IF EXISTS transactions_user_occurred_idx"))
        await conn.execute(
            text(
                "CREATE INDEX transactions_user_occurred_idx "
                "ON transactions (user_id, occurred_at DESC)"
            )
        )
        # Сид 18 системных категорий — копия из alembic 0002.
        from app.seed.system_categories import SYSTEM_CATEGORIES

        for icon, name, kind in SYSTEM_CATEGORIES:
            await conn.execute(
                text(
                    "INSERT INTO categories (user_id, name, kind, icon) "
                    "VALUES (NULL, :name, :kind, :icon)"
                ),
                {"name": name, "kind": kind, "icon": icon},
            )

    yield engine
    await engine.dispose()


# Список таблиц, которые TRUNCATE-аются между тестами. Системные категории
# (`user_id IS NULL`) переживают — это сид-данные.
USER_TABLES = ["transactions", "receipts", "budgets", "goals", "accounts"]


@pytest_asyncio.fixture
async def db_session(test_engine, request) -> AsyncIterator[AsyncSession]:
    """Свежая AsyncSession для теста. Перед началом — TRUNCATE пользовательских
    таблиц + DELETE non-system категорий, чтобы предыдущий тест не влиял.

    Класс с маркером @pytest.mark.no_rollback получает не-truncate'нутую БД —
    отвечает за свой setup/teardown сам.
    """
    no_rollback = request.node.get_closest_marker("no_rollback") is not None

    if not no_rollback:
        async with test_engine.begin() as conn:
            await conn.execute(
                text(f"TRUNCATE {', '.join(USER_TABLES)}, users RESTART IDENTITY CASCADE")
            )
            await conn.execute(text("DELETE FROM categories WHERE user_id IS NOT NULL"))

    SessionLocal = async_sessionmaker(test_engine, expire_on_commit=False)
    async with SessionLocal() as session:
        yield session


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "no_rollback: тест/класс делает свой setup БД; conftest НЕ truncate'ит "
        "таблицы перед ним. Использовать для миграционных и concurrency-тестов.",
    )
