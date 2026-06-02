"""Smoke-тест миграции 0004 (planned_operations + categories.parent_id +
transactions.planned_operation_id/occurrence_date + unique partial index).

Покрывает: alembic upgrade head проходит на чистой БД без SQL/op.* ошибок;
downgrade -1 откатывает структурно (drop_index ДО drop_column — иначе
Postgres «cannot drop column because index depends on it»).

Backfill отсутствует (все новые колонки nullable / с server_default), на VPS
накатывается без downtime'a через migrate-then-serve (ADR-0006); smoke
на копии prod-БД защищает от структурных регрессов.
"""

import asyncio
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MIGRATION_DB = "pulse_test_migration_0004"
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
class TestMigration0004Smoke:
    async def test_upgrade_to_head_creates_planned_and_extensions(self):
        await _recreate_db()
        cfg = _alembic_cfg()
        # Поднимаем до 0004 явно (не head) — будущие миграции (0005+) могут
        # переименовать/удалить колонки, на которые опираются assertion'ы.
        await asyncio.to_thread(command.upgrade, cfg, "0004_planned_operations")

        engine = create_async_engine(MIGRATION_URL)
        try:
            async with engine.connect() as conn:
                # planned_operations создана.
                assert (
                    await conn.scalar(text("SELECT to_regclass('planned_operations')"))
                ) is not None

                # categories.parent_id есть.
                has_parent = await conn.scalar(
                    text(
                        "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                        "WHERE table_name='categories' AND column_name='parent_id')"
                    )
                )
                assert has_parent

                # transactions расширена двумя колонками.
                for col in ("planned_operation_id", "occurrence_date"):
                    exists = await conn.scalar(
                        text(
                            "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                            f"WHERE table_name='transactions' AND column_name='{col}')"
                        )
                    )
                    assert exists, f"transactions.{col} отсутствует"

                # partial unique transactions_planned_uq существует.
                idx = await conn.scalar(
                    text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE tablename='transactions' "
                        "AND indexname='transactions_planned_uq'"
                    )
                )
                assert idx == "transactions_planned_uq"

                # alembic_version = head.
                v = await conn.scalar(text("SELECT version_num FROM alembic_version"))
                assert v == "0004_planned_operations"
        finally:
            await engine.dispose()

    async def test_downgrade_to_0003_succeeds(self):
        await _recreate_db()
        cfg = _alembic_cfg()
        # Целевая ревизия — явно (не "-1"), иначе тест сломается с появлением
        # 0005 (-1 уйдёт уже в 0004, а не в 0003).
        await asyncio.to_thread(command.upgrade, cfg, "0004_planned_operations")
        # drop_index ДО drop_column — порядок критичен; если перепутать,
        # Postgres вернёт «cannot drop column because index depends on it».
        await asyncio.to_thread(command.downgrade, cfg, "0003_workspaces_rekey")

        engine = create_async_engine(MIGRATION_URL)
        try:
            async with engine.connect() as conn:
                # planned_operations ушла.
                assert (
                    await conn.scalar(text("SELECT to_regclass('planned_operations')"))
                ) is None
                # planned-колонки ушли с transactions.
                for col in ("planned_operation_id", "occurrence_date"):
                    exists = await conn.scalar(
                        text(
                            "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                            f"WHERE table_name='transactions' AND column_name='{col}')"
                        )
                    )
                    assert not exists, f"transactions.{col} должен быть дропнут"
                # categories.parent_id ушла.
                has_parent = await conn.scalar(
                    text(
                        "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                        "WHERE table_name='categories' AND column_name='parent_id')"
                    )
                )
                assert not has_parent
        finally:
            await engine.dispose()
