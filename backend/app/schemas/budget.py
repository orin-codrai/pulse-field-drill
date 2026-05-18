from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

BudgetPeriod = Literal["week", "month", "year"]


class BudgetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_id: int
    period: BudgetPeriod
    limit_minor: int = Field(gt=0)
    starts_on: date
    ends_on: date | None = None


class BudgetUpdate(BaseModel):
    """Whitelist: только limit_minor / ends_on / archived_at. category_id,
    period, starts_on — иммутабельны (поменять = создать новый бюджет).
    """

    model_config = ConfigDict(extra="forbid")

    limit_minor: int | None = Field(default=None, gt=0)
    ends_on: date | None = None
    archived_at: datetime | None = None


class BudgetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    category_id: int
    period: str
    limit_minor: int
    currency: str
    starts_on: date
    ends_on: date | None
    archived_at: datetime | None
    created_at: datetime


class BudgetStatusItem(BaseModel):
    budget_id: int
    category_name: str
    period: str
    spent_minor: int
    limit_minor: int
    percent: float
    period_ends_on: date
