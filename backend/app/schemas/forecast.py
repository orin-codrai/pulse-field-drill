from datetime import date

from pydantic import BaseModel, ConfigDict


class ForecastOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    available_now: int
    reserved: int
    planned_income: int
    planned_expense: int
    planned_skim: int
    projected_balance: int
    projected_available: int
    horizon: date
