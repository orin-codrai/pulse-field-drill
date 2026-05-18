"""Stub router. Полные тела + Pydantic схемы — Sprint 3b.

URL-пространство застолблено, GET возвращают пусто, mutations → 501.
Фронт может писать код против stable contract'a уже сейчас.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.deps import current_user
from app.models import User

router = APIRouter(prefix="/goals", tags=["goals"])

_NOT_IMPLEMENTED = "not implemented in Sprint 3a; see Sprint 3b plan"


@router.get("")
async def list_goals(user: User = Depends(current_user)) -> list:
    return []


@router.post("", status_code=status.HTTP_501_NOT_IMPLEMENTED)
async def create_goal(user: User = Depends(current_user)) -> None:
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, _NOT_IMPLEMENTED)


@router.patch("/{goal_id}", status_code=status.HTTP_501_NOT_IMPLEMENTED)
async def update_goal(goal_id: int, user: User = Depends(current_user)) -> None:
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, _NOT_IMPLEMENTED)


@router.delete("/{goal_id}", status_code=status.HTTP_501_NOT_IMPLEMENTED)
async def delete_goal(goal_id: int, user: User = Depends(current_user)) -> None:
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, _NOT_IMPLEMENTED)


@router.get("/{goal_id}/progress")
async def goal_progress(goal_id: int, user: User = Depends(current_user)) -> None:
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, _NOT_IMPLEMENTED)
