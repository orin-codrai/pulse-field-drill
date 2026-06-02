from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PlannedKind = Literal["income", "expense"]
PlannedRecurrence = Literal["once", "week", "month", "year"]
PlannedStatus = Literal["planned", "paused", "done"]


class PlannedOperationCreate(BaseModel):
    """POST /api/planned. currency не принимаем — фикс RUB через server_default."""

    model_config = ConfigDict(extra="forbid")

    kind: PlannedKind
    amount_minor: int = Field(gt=0)
    # MF10-1: NOT NULL для всех kind (выровнено с tx XOR CHECK).
    category_id: int
    account_id: int
    first_date: date
    recurrence: PlannedRecurrence
    total_cycles: int | None = Field(default=None, ge=1)
    note: str | None = Field(default=None, max_length=2000)


class PlannedOperationUpdate(BaseModel):
    """PATCH /api/planned/{id}. Whitelist + extra='forbid' защищает
    completed_cycles от mass-assignment (иначе юзер PATCH'нул бы =999 и
    confirm перепрыгнул бы вхождения).

    'done' НЕ принимаем — статус выставляет только confirm. paused→planned
    = resume (completed_cycles сохраняется; следующий confirm с n=completed,
    не сброс).
    """

    model_config = ConfigDict(extra="forbid")

    amount_minor: int | None = Field(default=None, gt=0)
    category_id: int | None = None
    account_id: int | None = None
    first_date: date | None = None
    recurrence: PlannedRecurrence | None = None
    total_cycles: int | None = Field(default=None, ge=1)
    status: Literal["planned", "paused"] | None = None
    note: str | None = Field(default=None, max_length=2000)
    archived_at: datetime | None = None


class PlannedOperationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    kind: PlannedKind
    amount_minor: int
    currency: str
    category_id: int
    account_id: int
    first_date: date
    recurrence: PlannedRecurrence
    total_cycles: int | None
    completed_cycles: int
    status: PlannedStatus
    note: str | None
    created_at: datetime
    archived_at: datetime | None
    # created_by_user_id намеренно не отдаём (privacy в shared workspace).


class DuePlannedItem(BaseModel):
    """GET /api/planned/due — первое неподтверждённое вхождение каждого
    активного плана со scheduled_date <= today."""

    model_config = ConfigDict(from_attributes=True)

    planned_operation_id: int
    scheduled_date: date
    amount_minor: int
    kind: PlannedKind
    currency: str
    category_id: int
    account_id: int
    note: str | None
