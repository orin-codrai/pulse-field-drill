import hashlib
import hmac
import json
import os
import time
from collections.abc import AsyncIterator
from urllib.parse import urlencode

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

BOT_TOKEN = "12345:test-bot-token"

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://pulse:devpass@localhost:5432/pulse_test",
)

# Settings — pydantic-settings singleton, читает env при первом импорте
# app.config. Задаём правильные значения ДО того, как любой тест импортит
# app.*. Тестовые initData подписаны под BOT_TOKEN — настройки приложения
# должны совпадать, иначе validate_init_data вернёт bad signature.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
os.environ.setdefault("DATABASE_URL", TEST_DATABASE_URL)


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
                "CREATE UNIQUE INDEX categories_ws_name_uq "
                "ON categories (COALESCE(workspace_id, 0), name) "
                "WHERE archived_at IS NULL"
            )
        )
        await conn.execute(text("DROP INDEX IF EXISTS transactions_ws_occurred_idx"))
        await conn.execute(
            text(
                "CREATE INDEX transactions_ws_occurred_idx "
                "ON transactions (workspace_id, occurred_at DESC)"
            )
        )
        # Сид 18 системных категорий — копия из alembic 0002.
        from app.seed.system_categories import SYSTEM_CATEGORIES

        for icon, name, kind in SYSTEM_CATEGORIES:
            await conn.execute(
                text(
                    "INSERT INTO categories (workspace_id, name, kind, icon) "
                    "VALUES (NULL, :name, :kind, :icon)"
                ),
                {"name": name, "kind": kind, "icon": icon},
            )

    yield engine
    await engine.dispose()


# Таблицы, которые TRUNCATE-аются между тестами. Системные категории
# (`workspace_id IS NULL`) пере-сидятся после (RESTART IDENTITY сбрасывает их id).
USER_TABLES = [
    "audit_log",
    "workspace_invites",
    "envelope_entries",
    "transactions",
    "planned_operations",
    "receipts",
    "budgets",
    "envelopes",
    "accounts",
    "categories",
    "workspace_members",
    "workspaces",
]


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
            # Все данные-таблицы + users, одним TRUNCATE (CASCADE покрывает FK
            # между ними). categories тоже сносится → re-seed системных ниже.
            await conn.execute(
                text(f"TRUNCATE {', '.join(USER_TABLES)}, users RESTART IDENTITY CASCADE")
            )
            from app.seed.system_categories import SYSTEM_CATEGORIES

            for icon, name, kind in SYSTEM_CATEGORIES:
                await conn.execute(
                    text(
                        "INSERT INTO categories (workspace_id, name, kind, icon) "
                        "VALUES (NULL, :name, :kind, :icon)"
                    ),
                    {"name": name, "kind": kind, "icon": icon},
                )

    SessionLocal = async_sessionmaker(test_engine, expire_on_commit=False)
    async with SessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def app_client(db_session) -> AsyncIterator[AsyncClient]:
    """FastAPI TestClient через httpx, с override get_session → test-session.

    Все endpoint'ы в тесте используют ту же транзакцию что и db_session
    фикстура — рукотворные mutations и API-ответы видны друг другу.
    """
    from app.db.session import get_session
    from app.main import app

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def provisioned_user(db_session, valid_user):
    """Юзер уже в БД с 2 default accounts. Эмулирует «фронт уже сходил
    в /api/me хотя бы раз»."""
    from app.schemas.user import TelegramUser
    from app.services.user_provisioning import ensure_user_provisioned

    tg = TelegramUser(**valid_user)
    user = await ensure_user_provisioned(db_session, tg)
    await db_session.commit()
    return user


@pytest_asyncio.fixture
async def workspace(db_session, provisioned_user):
    """Personal workspace провиженного юзера — owner-scope для данных в тестах.
    Тесты создают Account/Transaction/etc. с workspace_id=workspace.id."""
    from app.models import Workspace

    return await db_session.get(Workspace, provisioned_user.active_workspace_id)


@pytest_asyncio.fixture
async def registered_user(db_session, provisioned_user):
    """Юзер прошёл /me/register — display_name + consent_at заполнены.
    Нужен для sharing-операций (POST /workspaces, /invites, /accept) после
    P7.D: `registered_user` dependency блокирует 412 без profile."""
    from datetime import datetime, timezone

    provisioned_user.display_name = "Orrin Registered"
    provisioned_user.consent_at = datetime.now(timezone.utc)
    await db_session.commit()
    return provisioned_user


@pytest.fixture
def auth_header(valid_init_data) -> dict[str, str]:
    return {"Authorization": f"tma {valid_init_data}"}


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "no_rollback: тест/класс делает свой setup БД; conftest НЕ truncate'ит "
        "таблицы перед ним. Использовать для миграционных и concurrency-тестов.",
    )
