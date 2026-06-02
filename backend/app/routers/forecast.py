from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_workspace
from app.db.session import get_session
from app.models import Workspace
from app.schemas.forecast import ForecastOut
from app.services.forecast import compute_forecast

router = APIRouter(prefix="/forecast", tags=["forecast"])


@router.get("", response_model=ForecastOut)
async def get_forecast(
    ws: Workspace = Depends(current_workspace),
    session: AsyncSession = Depends(get_session),
    horizon: date | None = Query(default=None),
) -> ForecastOut:
    """Прогноз баланса на горизонте. По умолчанию — конец текущего месяца.
    horizon > today + 13mo clamp'нется (тихо, не 422)."""
    return ForecastOut.model_validate(
        await compute_forecast(session, ws.id, horizon)
    )
