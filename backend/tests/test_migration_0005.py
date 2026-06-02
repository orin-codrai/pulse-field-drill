"""Smoke-тест миграции 0005 (envelopes from goals + envelope_entries + tx FK→RESTRICT).

Покрывает: alembic upgrade проходит на чистой БД; downgrade откатывает
структурно (с guards на envelope_entries и target_amount_minor IS NULL).

Backfill отсутствует (goals пустая), всё структурное. Backfill на
реальных данных — manual smoke на staging-копии prod через
infra/scripts/migrate-smoke.sh.
"""

import asyncio
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MIGRATION_DB = "pulse_test_migration_0005"
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
class TestMigration0005Smoke:
    async def test_upgrade_to_head_renames_goals_creates_entries(self):
        await _recreate_db()
        cfg = _alembic_cfg()
        # Поднимаем до 0005 явно — будущие миграции (0006+) могут изменить
        # колонки/таблицы, на которые опираются assertion'ы.
        await asyncio.to_thread(command.upgrade, cfg, "0005_envelopes_from_goals")

        engine = create_async_engine(MIGRATION_URL)
        try:
            async with engine.connect() as conn:
                # goals → envelopes.
                assert (
                    await conn.scalar(text("SELECT to_regclass('envelopes')"))
                ) is not None
                assert (
                    await conn.scalar(text("SELECT to_regclass('goals')"))
                ) is None

                # envelopes.percent есть, target_amount_minor nullable,
                # linked_account_id ушёл.
                cols = await conn.execute(
                    text(
                        "SELECT column_name, is_nullable "
                        "FROM information_schema.columns "
                        "WHERE table_name='envelopes'"
                    )
                )
                col_map = {r.column_name: r.is_nullable for r in cols}
                assert "percent" in col_map
                assert col_map["target_amount_minor"] == "YES"
                assert "linked_account_id" not in col_map

                # envelope_entries создана.
                assert (
                    await conn.scalar(text("SELECT to_regclass('envelope_entries')"))
                ) is not None

                # Композитный FK на envelopes(id, workspace_id).
                fk = await conn.scalar(text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conname='envelope_entries_envelope_fkey'"
                ))
                assert fk == "envelope_entries_envelope_fkey"

                # Constraint имена приведены к envelopes_*.
                for name in (
                    "envelopes_pkey",
                    "envelopes_percent_chk",
                    "envelopes_target_chk",
                    "envelopes_currency_chk",
                    "envelopes_workspace_id_fkey",
                    "envelopes_id_ws_uq",
                ):
                    found = await conn.scalar(text(
                        f"SELECT conname FROM pg_constraint WHERE conname='{name}'"
                    ))
                    assert found == name, f"missing constraint {name}"

                # tx planned_operation_id_fkey теперь RESTRICT (C13-1).
                # pg_constraint.confdeltype возвращает char(1) как bytes в asyncpg.
                action = await conn.scalar(text(
                    "SELECT confdeltype FROM pg_constraint "
                    "WHERE conname='transactions_planned_operation_id_fkey'"
                ))
                assert action == b"r", f"expected RESTRICT (b'r'), got {action!r}"

                # alembic_version = head.
                v = await conn.scalar(text("SELECT version_num FROM alembic_version"))
                assert v == "0005_envelopes_from_goals"
        finally:
            await engine.dispose()

    async def test_downgrade_to_0004_succeeds_on_empty_db(self):
        await _recreate_db()
        cfg = _alembic_cfg()
        await asyncio.to_thread(command.upgrade, cfg, "0005_envelopes_from_goals")
        await asyncio.to_thread(command.downgrade, cfg, "0004_planned_operations")

        engine = create_async_engine(MIGRATION_URL)
        try:
            async with engine.connect() as conn:
                # envelope_entries ушла.
                assert (
                    await conn.scalar(text("SELECT to_regclass('envelope_entries')"))
                ) is None
                # envelopes → обратно goals.
                assert (
                    await conn.scalar(text("SELECT to_regclass('envelopes')"))
                ) is None
                assert (
                    await conn.scalar(text("SELECT to_regclass('goals')"))
                ) is not None
                # goals.linked_account_id вернулся.
                has_linked = await conn.scalar(text(
                    "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                    "WHERE table_name='goals' AND column_name='linked_account_id')"
                ))
                assert has_linked
                # goals.percent ушёл.
                has_pct = await conn.scalar(text(
                    "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                    "WHERE table_name='goals' AND column_name='percent')"
                ))
                assert not has_pct
                # tx FK обратно SET NULL.
                action = await conn.scalar(text(
                    "SELECT confdeltype FROM pg_constraint "
                    "WHERE conname='transactions_planned_operation_id_fkey'"
                ))
                assert action == b"n", f"expected SET NULL (b'n'), got {action!r}"
        finally:
            await engine.dispose()
