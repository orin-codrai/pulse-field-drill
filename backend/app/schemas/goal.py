from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class GoalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    target_amount_minor: int = Field(gt=0)
    target_date: date | None = None
    linked_account_id: int | None = None
    icon: str | None = Field(default=None, max_length=64)


class GoalUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    target_amount_minor: int | None = Field(default=None, gt=0)
    target_date: date | None = None
    linked_account_id: int | None = None
    icon: str | None = Field(default=None, max_length=64)
    archived_at: datetime | None = None


class GoalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    target_amount_minor: int
    currency: str
    target_date: date | None
    linked_account_id: int | None
    icon: str | None
    archived_at: datetime | None
    created_at: datetime


class GoalProgress(BaseModel):
    current_minor: int
    target_minor: int
    percent: float
    days_left: int | None
    on_track: bool
