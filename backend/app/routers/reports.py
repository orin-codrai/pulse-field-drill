"""Stub router. Полные тела + Pydantic схемы — Sprint 3b."""

from fastapi import APIRouter, Depends

from app.auth.deps import current_user
from app.models import User

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/month")
async def month_report(
    user: User = Depends(current_user),
    year: int | None = None,
    month: int | None = None,
) -> dict:
    return {"by_category": {}, "by_kind": {}, "total_expense": 0, "total_income": 0}


@router.get("/calendar")
async def calendar_report(
    user: User = Depends(current_user),
) -> list:
    return []
