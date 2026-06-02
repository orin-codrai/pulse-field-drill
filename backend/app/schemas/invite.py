from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


InviteStatus = Literal["pending", "accepted", "revoked", "expired"]


class InviteOut(BaseModel):
    """Полная карточка invite — для list owner'ом и для response accept."""

    model_config = ConfigDict(from_attributes=True)
    id: int
    workspace_id: int
    token: str
    status: InviteStatus
    expires_at: datetime
    created_at: datetime
    accepted_at: datetime | None


class InvitePreview(BaseModel):
    """Public-ish preview для accept screen на frontend. БЕЗ token (он уже в
    URL) и БЕЗ created_by ID (privacy: имя только)."""

    model_config = ConfigDict(from_attributes=True)
    workspace_id: int
    workspace_name: str
    workspace_kind: Literal["personal", "shared"]
    status: InviteStatus
    expires_at: datetime
    inviter_display_name: str | None  # display_name или fallback на first_name
