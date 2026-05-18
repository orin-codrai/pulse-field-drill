from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AccountType = Literal["card", "cash", "savings", "debt", "credit"]


class AccountCreate(BaseModel):
    """POST /api/accounts body. `currency` сервер пинит в 'RUB' — клиент не
    может передать; добавление поля сюда — осознанная смена контракта."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    type: AccountType
    initial_balance_minor: int = 0
    icon: str | None = Field(default=None, max_length=64)


class AccountUpdate(BaseModel):
    """PATCH /api/accounts/{id}. Whitelist mutable полей.

    `archived_at=None` (явный JSON null) разархивирует. Отсутствие поля
    в payload — не трогает значение. `exclude_unset=True` в handler.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    icon: str | None = Field(default=None, max_length=64)
    archived_at: datetime | None = None


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    type: str
    currency: str
    initial_balance_minor: int
    icon: str | None
    archived_at: datetime | None
    created_at: datetime
