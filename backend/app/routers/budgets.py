"""Stub router. Полные тела + Pydantic схемы — Sprint 3b."""

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.deps import current_user
from app.models import User

router = APIRouter(prefix="/budgets", tags=["budgets"])

_NOT_IMPLEMENTED = "not implemented in Sprint 3a; see Sprint 3b plan"


@router.get("")
async def list_budgets(user: User = Depends(current_user)) -> list:
    return []


@router.post("", status_code=status.HTTP_501_NOT_IMPLEMENTED)
async def create_budget(user: User = Depends(current_user)) -> None:
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, _NOT_IMPLEMENTED)


@router.patch("/{budget_id}", status_code=status.HTTP_501_NOT_IMPLEMENTED)
async def update_budget(budget_id: int, user: User = Depends(current_user)) -> None:
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, _NOT_IMPLEMENTED)


@router.delete("/{budget_id}", status_code=status.HTTP_501_NOT_IMPLEMENTED)
async def delete_budget(budget_id: int, user: User = Depends(current_user)) -> None:
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, _NOT_IMPLEMENTED)


@router.get("/status")
async def budgets_status(user: User = Depends(current_user)) -> list:
    return []
