from fastapi import Header, HTTPException, status

from app.auth.init_data import InvalidInitData, validate_init_data
from app.config import settings
from app.schemas.user import TelegramUser


def current_user(authorization: str = Header(...)) -> TelegramUser:
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
