"""Smoke-тест миграции 0003 (workspaces + re-key) на чистой БД.

Покрывает:
- alembic upgrade 0001 → 0002 → 0003 проходит без SQL/op.* ошибок.
- После накатки: 0 юзеров → 0 workspaces (триггерный backfill не падает на пустой
  таблице users), системные категории (18) → workspace_id IS NULL.
- alembic downgrade head → -1 возвращает к 0002-state без shared workspace.

Backfill-correctness на реальных данных проверяется руками на staging-копии
prod-БД перед `git push` (план §Phase 4: «бэкап БД до»). Pytest-харнес был бы
длиннее самой миграции; smoke ловит большинство багов (синтаксис SQL,
неправильные имена FK/индексов, типы колонок).
"""

import asyncio
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MIGRATION_DB = "pulse_test_migration_0003"
ADMIN_URL = "postgresql+asyncpg://pulse:devpass@localhost:5432/postgres"
MIGRATION_URL = f"postgresql+asyncpg://pulse:devpass@localhost:5432/{MIGRATION_DB}"


def _alembic_cfg() -> Config:
    cfg = Config(os.path.join(PROJECT_ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(PROJECT_ROOT, "alembic"))
    cfg.set_main_option("sqlalchemy.url", MIGRATION_URL)
    return cfg


async def _recreate_db() -> None:
    """Drop+create MIGRATION_DB. AUTOCOMMIT обязателен для DROP/CREATE DATABASE."""
    admin = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f"DROP DATABASE IF EXISTS {MIGRATION_DB}"))
        await conn.execute(text(f"CREATE DATABASE {MIGRATION_DB}"))
    await admin.dispose()


@pytest.mark.no_rollback
class TestMigration0003Smoke:
    """`no_rollback` — мы не трогаем основную тестовую БД (`pulse_test`);
    каждый тест поднимает свою и сносит её."""

    async def test_upgrade_to_head_succeeds_on_empty_db(self):
        await _recreate_db()
        cfg = _alembic_cfg()
        # asyncio.to_thread: command.* — sync, а env.py внутри запускает
        # asyncio.run() для async-движка. Из sync thread это безопасно.
        await asyncio.to_thread(command.upgrade, cfg, "head")

        # Проверяем что схема 0003-state: workspaces существует, на 6 таблицах
        # workspace_id есть, user_id колонок нет, системные категории видны.
        engine = create_async_engine(MIGRATION_URL)
        try:
            async with engine.connect() as conn:
                ws_exists = await conn.scalar(
                    text("SELECT to_regclass('workspaces')")
                )
                assert ws_exists is not None

                wm_exists = await conn.scalar(
                    text("SELECT to_regclass('workspace_members')")
                )
                assert wm_exists is not None

                # У всех 6 владеемых таблиц должна быть workspace_id колонка.
                for tbl in ("accounts", "transactions", "categories", "budgets", "goals", "receipts"):
                    has_ws = await conn.scalar(
                        text(
                            "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                            f"WHERE table_name='{tbl}' AND column_name='workspace_id')"
                        )
                    )
                    assert has_ws, f"{tbl}.workspace_id отсутствует"

                    has_user = await conn.scalar(
                        text(
                            "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                            f"WHERE table_name='{tbl}' AND column_name='user_id')"
                        )
                    )
                    assert not has_user, f"{tbl}.user_id должен быть дропнут"

                # 0002 seed: 18 системных категорий, у всех workspace_id IS NULL.
                n_system = await conn.scalar(
                    text(
                        "SELECT count(*) FROM categories WHERE workspace_id IS NULL"
                    )
                )
                assert n_system == 18

                # created_by_user_id появился на accounts и transactions.
                for tbl in ("accounts", "transactions"):
                    has_audit = await conn.scalar(
                        text(
                            "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                            f"WHERE table_name='{tbl}' AND column_name='created_by_user_id')"
                        )
                    )
                    assert has_audit, f"{tbl}.created_by_user_id отсутствует"
        finally:
            await engine.dispose()

    async def test_downgrade_to_0002_succeeds_when_no_shared_workspace(self):
        await _recreate_db()
        cfg = _alembic_cfg()
        # Поднимаем до 0003 явно (не head) — иначе по мере добавления
        # будущих миграций downgrade -1 уйдёт не туда и assertion'ы протухнут.
        await asyncio.to_thread(command.upgrade, cfg, "0003_workspaces_rekey")
        await asyncio.to_thread(command.downgrade, cfg, "7111ba4f0334")

        engine = create_async_engine(MIGRATION_URL)
        try:
            async with engine.connect() as conn:
                # После downgrade: user_id колонки вернулись, workspaces ушли.
                ws_gone = await conn.scalar(
                    text("SELECT to_regclass('workspaces')")
                )
                assert ws_gone is None

                for tbl in ("accounts", "transactions", "categories", "budgets", "goals", "receipts"):
                    has_user = await conn.scalar(
                        text(
                            "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                            f"WHERE table_name='{tbl}' AND column_name='user_id')"
                        )
                    )
                    assert has_user, f"{tbl}.user_id должен вернуться при downgrade"
        finally:
            await engine.dispose()
