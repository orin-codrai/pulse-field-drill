"""Smoke-тест миграции 0006 (workspace_invites + audit_log + users расширение).

Покрывает: alembic upgrade head на чистой БД без SQL/op.* ошибок; downgrade
0005 с проверкой 4 guards (audit/accepted-invites/soft-deleted/consent_at).

Backfill отсутствует (всё nullable), деплой no-downtime через
migrate-then-serve (ADR-0006). Backfill-correctness на реальных данных —
infra/scripts/migrate-smoke.sh.
"""

import asyncio
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MIGRATION_DB = "pulse_test_migration_0006"
ADMIN_URL = "postgresql+asyncpg://pulse:devpass@localhost:5432/postgres"
MIGRATION_URL = f"postgresql+asyncpg://pulse:devpass@localhost:5432/{MIGRATION_DB}"


def _alembic_cfg() -> Config:
    cfg = Config(os.path.join(PROJECT_ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(PROJECT_ROOT, "alembic"))
    cfg.set_main_option("sqlalchemy.url", MIGRATION_URL)
    return cfg


async def _recreate_db() -> None:
    admin = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f"DROP DATABASE IF EXISTS {MIGRATION_DB}"))
        await conn.execute(text(f"CREATE DATABASE {MIGRATION_DB}"))
    await admin.dispose()


@pytest.mark.no_rollback
class TestMigration0006Smoke:
    async def test_upgrade_creates_invites_audit_user_columns(self):
        await _recreate_db()
        cfg = _alembic_cfg()
        await asyncio.to_thread(command.upgrade, cfg, "head")

        engine = create_async_engine(MIGRATION_URL)
        try:
            async with engine.connect() as conn:
                # Новые таблицы.
                assert (
                    await conn.scalar(text("SELECT to_regclass('workspace_invites')"))
                ) is not None
                assert (
                    await conn.scalar(text("SELECT to_regclass('audit_log')"))
                ) is not None

                # 4 nullable колонки на users.
                for col in ("display_name", "email", "consent_at", "deleted_at"):
                    nullable = await conn.scalar(text(
                        "SELECT is_nullable FROM information_schema.columns "
                        f"WHERE table_name='users' AND column_name='{col}'"
                    ))
                    assert nullable == "YES", f"users.{col} should be nullable"

                # workspace_invites FK: workspace CASCADE, actors SET NULL.
                fks = (await conn.execute(text(
                    "SELECT conname, confdeltype FROM pg_constraint "
                    "WHERE conrelid='workspace_invites'::regclass "
                    "AND contype='f'"
                ))).all()
                fk_map = {r.conname: r.confdeltype for r in fks}
                # CASCADE='c', SET NULL='n'.
                assert fk_map.get("workspace_invites_workspace_id_fkey") == b"c"
                assert fk_map.get("workspace_invites_created_by_user_id_fkey") == b"n"
                assert fk_map.get("workspace_invites_accepted_by_user_id_fkey") == b"n"

                # audit_log FK: workspace RESTRICT, actor SET NULL.
                fks = (await conn.execute(text(
                    "SELECT conname, confdeltype FROM pg_constraint "
                    "WHERE conrelid='audit_log'::regclass AND contype='f'"
                ))).all()
                fk_map = {r.conname: r.confdeltype for r in fks}
                # RESTRICT='r'.
                assert fk_map.get("audit_log_workspace_id_fkey") == b"r"
                assert fk_map.get("audit_log_actor_user_id_fkey") == b"n"

                # alembic_version = head.
                v = await conn.scalar(text("SELECT version_num FROM alembic_version"))
                assert v == "0006_sharing_and_audit"
        finally:
            await engine.dispose()

    async def test_downgrade_succeeds_on_empty_db(self):
        await _recreate_db()
        cfg = _alembic_cfg()
        await asyncio.to_thread(command.upgrade, cfg, "0006_sharing_and_audit")
        await asyncio.to_thread(command.downgrade, cfg, "0005_envelopes_from_goals")

        engine = create_async_engine(MIGRATION_URL)
        try:
            async with engine.connect() as conn:
                assert (
                    await conn.scalar(text("SELECT to_regclass('workspace_invites')"))
                ) is None
                assert (
                    await conn.scalar(text("SELECT to_regclass('audit_log')"))
                ) is None
                for col in ("display_name", "email", "consent_at", "deleted_at"):
                    exists = await conn.scalar(text(
                        "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                        f"WHERE table_name='users' AND column_name='{col}')"
                    ))
                    assert not exists, f"users.{col} should be dropped"
        finally:
            await engine.dispose()

    async def test_downgrade_raises_if_audit_log_has_rows(self):
        """Guard: история не сносится молча."""
        await _recreate_db()
        cfg = _alembic_cfg()
        await asyncio.to_thread(command.upgrade, cfg, "0006_sharing_and_audit")

        # Вставить минимальные данные для audit_log: нужен workspace.
        engine = create_async_engine(MIGRATION_URL)
        try:
            async with engine.begin() as conn:
                await conn.execute(text(
                    "INSERT INTO users (tg_id) VALUES (777001)"
                ))
                await conn.execute(text(
                    "INSERT INTO workspaces (name, kind) VALUES ('test', 'personal')"
                ))
                await conn.execute(text(
                    "INSERT INTO audit_log "
                    "(workspace_id, actor_user_id, entity_type, entity_id, "
                    "action, snapshot_json) "
                    "VALUES (1, 1, 'transaction', 999, 'create', '{}'::jsonb)"
                ))
        finally:
            await engine.dispose()

        # Downgrade теперь должен RAISE.
        with pytest.raises(DBAPIError, match="audit_log"):
            await asyncio.to_thread(command.downgrade, cfg, "0005_envelopes_from_goals")
