import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.config import settings
from app.db.base import Base
import app.models  # noqa: F401  — регистрирует все таблицы на Base.metadata.

config = context.config

# Подставляем URL из настроек, не из alembic.ini (там — плейсхолдер).
# Если caller уже выставил URL через `config.set_main_option` (тесты миграций
# гоняют alembic на отдельной БД) — не перетираем.
_existing = config.get_main_option("sqlalchemy.url")
if not _existing or _existing.startswith("driver://"):
    config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


# Expression-based индексы (COALESCE, DESC) живут только в миграции через op.execute —
# autogenerate не умеет их сравнить с моделью и пытается их «удалить» на каждом
# alembic check. Игнорируем их в diff.
INDEXES_MANUAL_ONLY = {
    "categories_ws_name_uq",
    "transactions_ws_occurred_idx",
}


def include_object(obj, name, type_, reflected, compare_to):  # type: ignore[no-untyped-def]
    if type_ == "index" and name in INDEXES_MANUAL_ONLY:
        return False
    return True


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
