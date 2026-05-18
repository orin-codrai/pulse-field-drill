from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TransactionKind = Literal["expense", "income", "transfer", "adjustment"]


class TransactionCreate(BaseModel):
    """POST body. CHECK transactions_kind_fields_chk на стороне БД сторожит
    валидные комбинации kind ↔ FK. Дополнительно application-level: FK
    должны указывать на ресурсы юзера; archived account → 422."""

    model_config = ConfigDict(extra="forbid")

    kind: TransactionKind
    amount_minor: int = Field(gt=0)
    from_account_id: int | None = None
    to_account_id: int | None = None
    category_id: int | None = None
    occurred_at: datetime | None = None
    note: str | None = Field(default=None, max_length=2000)


class TransactionUpdate(BaseModel):
    """PATCH whitelist: только note и occurred_at. Сумма/тип/счета/категория
    иммутабельны — чтобы починить, удаляешь+создаёшь новую."""

    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=2000)
    occurred_at: datetime | None = None


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    amount_minor: int
    currency: str
    from_account_id: int | None
    to_account_id: int | None
    category_id: int | None
    receipt_id: int | None
    occurred_at: datetime
    note: str | None
    created_at: datetime
