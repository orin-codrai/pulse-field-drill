"""Manual CLI для maintenance операций.

v1 содержит только `purge-deleted-users` (PIN-L: cron — backlog).

Использование:
    python -m app.cli purge-deleted-users

Выводит список soft-deleted юзеров с возрастом deleted_at >=
PURGE_AFTER_DAYS, физически удаляет каждого через `services.purge.
hard_purge_user`. Запуск из maintenance окна оператором.
"""

import asyncio
import sys
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models import User
from app.routers.me import PURGE_AFTER_DAYS
from app.services.purge import _now_utc, hard_purge_user


async def purge_deleted_users() -> int:
    """Возвращает число удалённых юзеров."""
    engine = create_async_engine(settings.database_url)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    cutoff = _now_utc() - timedelta(days=PURGE_AFTER_DAYS)
    purged = 0
    try:
        async with SessionLocal() as session:  # type: AsyncSession
            users = (await session.execute(
                select(User).where(
                    User.deleted_at.is_not(None),
                    User.deleted_at <= cutoff,
                )
            )).scalars().all()
            for user in users:
                print(
                    f"purging user id={user.id} tg_id={user.tg_id} "
                    f"deleted_at={user.deleted_at.isoformat()}"
                )
                await hard_purge_user(session, user)
                purged += 1
    finally:
        await engine.dispose()
    return purged


def main() -> int:
    if len(sys.argv) < 2:
        print(
            "usage: python -m app.cli <command>\n"
            "commands: purge-deleted-users",
            file=sys.stderr,
        )
        return 2
    cmd = sys.argv[1]
    if cmd == "purge-deleted-users":
        n = asyncio.run(purge_deleted_users())
        print(f"purged {n} user(s)")
        return 0
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
