from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EnvelopeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    # NULL = ручной (без авто-скима); 1..100 = доля каждого дохода.
    percent: int | None = Field(default=None, gt=0, le=100)
    target_amount_minor: int | None = Field(default=None, gt=0)
    target_date: date | None = None
    icon: str | None = Field(default=None, max_length=64)


class EnvelopeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    percent: int | None = Field(default=None, gt=0, le=100)
    target_amount_minor: int | None = Field(default=None, gt=0)
    target_date: date | None = None
    icon: str | None = Field(default=None, max_length=64)
    archived_at: datetime | None = None


class EnvelopeOut(BaseModel):
    """В list-эндпоинте `reserved_minor` агрегируется на сервере; модель
    Envelope таких колонок не имеет, поэтому формируем отдельно в роутере."""

    model_config = ConfigDict(from_attributes=True)
    id: int
    workspace_id: int
    name: str
    percent: int | None
    target_amount_minor: int | None
    currency: str
    target_date: date | None
    icon: str | None
    archived_at: datetime | None
    created_at: datetime
    reserved_minor: int  # вычисляется в роутере


# Только manual/withdraw принимаются через API; skim создаётся skim_on_income
# сервером при income tx (C2 pass 11).
EnvelopeEntryKindIn = Literal["manual", "withdraw"]


class EnvelopeEntryCreate(BaseModel):
    """POST /envelopes/{id}/entries. amount_minor всегда положительное в
    payload; для withdraw сервер сохранит как отрицательное (PIN-C: signed
    semantic в БД, прозрачное Σ для reserved). MF1: created_by_user_id
    server-side из current_user — не в схеме."""

    model_config = ConfigDict(extra="forbid")
    kind: EnvelopeEntryKindIn
    amount_minor: int = Field(gt=0)


class EnvelopeEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    envelope_id: int
    workspace_id: int
    # Signed: withdraw < 0, manual/skim > 0. Frontend применяет abs+color.
    amount_minor: int
    kind: Literal["skim", "manual", "withdraw"]
    source_transaction_id: int | None
    created_at: datetime
