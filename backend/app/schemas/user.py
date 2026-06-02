from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class TelegramUser(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    first_name: str
    last_name: str | None = None
    username: str | None = None
    language_code: str | None = None
    is_premium: bool | None = None
    allows_write_to_pm: bool | None = None
    added_to_attachment_menu: bool | None = None
    photo_url: str | None = None


class MeOut(BaseModel):
    """GET /api/me response: TG fields + БД state. MF14-6: НЕ
    `from_attributes=True` — конструируется явно в `_build_me_out`, иначе
    SQLAlchemy подтянет `User.id` (ORM id) в поле `id` и сломает frontend,
    который читает `me.id` как tg_id.

    `internal_id` (MF14-6, renamed from `user_id`): внутренний ORM User.id.
    Используется ТОЛЬКО на frontend в AuditHistoryPage для «вы»-маркера
    через `audit.actor_user_id === me.internal_id`. Сервер никогда не
    accept'ит его в URL/body — защита от accidental escalation.
    """

    id: int  # tg_id (frontend читает body.id как tg_id, не ломать совместимость)
    first_name: str
    last_name: str | None
    username: str | None
    language_code: str | None
    is_premium: bool | None
    photo_url: str | None
    internal_id: int  # ORM User.id (для audit UI «это я»)
    active_workspace_id: int | None
    display_name: str | None
    email: str | None
    consent_at: datetime | None
    deleted_at: datetime | None
    registration_required: bool  # True если display_name OR consent_at NULL


class RegistrationBody(BaseModel):
    """POST /api/me/register. MF14-5: `consent: Literal[True]` — Pydantic
    отбивает False автоматом → 422 без runtime check (защита от
    PYTHONOPTIMIZE=1 на `assert body.consent`)."""

    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=1, max_length=200)
    email: EmailStr | None = None
    consent: Literal[True]
