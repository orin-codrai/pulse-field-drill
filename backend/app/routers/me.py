from fastapi import APIRouter, Depends

from app.auth.deps import current_user
from app.schemas.user import TelegramUser

router = APIRouter()


@router.get("/me", response_model=TelegramUser)
def me(user: TelegramUser = Depends(current_user)) -> TelegramUser:
    return user
