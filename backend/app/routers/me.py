from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import tg_user_from_auth
from app.db.session import get_session
from app.schemas.user import TelegramUser
from app.services.user_provisioning import ensure_user_provisioned

router = APIRouter()


@router.get("/me", response_model=TelegramUser)
async def me(
    tg_user: TelegramUser = Depends(tg_user_from_auth),
    session: AsyncSession = Depends(get_session),
) -> TelegramUser:
    """Первый touchpoint юзера. Триггерит idempotent provisioning
    (создаёт ORM user + 2 default accounts если ещё нет) и возвращает
    те данные, что прислал Telegram (initData).

    Все остальные endpoints полагаются на current_user — он 401-ит,
    если в БД ничего нет, поэтому /api/me обязан быть первым.
    """
    await ensure_user_provisioned(session, tg_user)
    await session.commit()
    return tg_user
