from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.init_data import InvalidInitData, validate_init_data
from app.config import settings
from app.db.session import get_session
from app.models import User, Workspace, WorkspaceMember
from app.schemas.user import TelegramUser


def tg_user_from_auth(authorization: str | None = Header(None)) -> TelegramUser:
    """Парсит `Authorization: tma <raw>` → валидирует HMAC → возвращает TelegramUser.

    Не трогает БД. Используется на /api/me перед provisioning'ом, и косвенно
    через current_user на остальных endpoint'ах.
    """
    if not authorization:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing auth")
    scheme, _, raw = authorization.partition(" ")
    if scheme.lower() != "tma" or not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad auth scheme")
    try:
        return validate_init_data(
            raw,
            settings.telegram_bot_token,
            max_age=settings.init_data_max_age,
        ).user
    except InvalidInitData as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e)) from e


async def current_user(
    tg_user: TelegramUser = Depends(tg_user_from_auth),
    session: AsyncSession = Depends(get_session),
) -> User:
    """Read-only lookup юзера в БД по tg_id. 401 если юзер не provision'ен.

    Контракт: первое обращение нового юзера обязательно через GET /api/me
    (фронт уже так делает, см. Sprint 2). На остальных endpoint'ах
    отсутствие юзера в БД = логическая ошибка фронта.
    """
    user = await session.scalar(select(User).where(User.tg_id == tg_user.id))
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "user not provisioned; call GET /api/me first",
        )
    return user


async def current_workspace(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Workspace:
    """Активный workspace юзера, с ре-валидацией membership на КАЖДОМ запросе.

    Не доверяет сохранённому `active_workspace_id` вслепую: проверяет, что юзер
    действительно член этого workspace. Это закрывает дыру, когда юзер мог бы
    выставить чужой workspace мимо switch-эндпоинта (см. ADR-0009 §4). Все
    роутеры фильтруют ресурсы по `workspace_id == current_workspace.id`.
    """
    if user.active_workspace_id is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "no active workspace; call GET /api/me first",
        )
    member = await session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == user.active_workspace_id,
            WorkspaceMember.user_id == user.id,
        )
    )
    if member is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "not a member of active workspace"
        )
    ws = await session.get(Workspace, user.active_workspace_id)
    if ws is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "active workspace missing"
        )
    return ws
