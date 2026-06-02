from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WorkspaceMember(Base):
    """Членство юзера в workspace. v1: оба участника shared — root.

    FK обе с ON DELETE CASCADE: удаление workspace или юзера убирает строку
    членства безопасно — shared-данные键ятся на workspace_id, не на user_id,
    поэтому переживают удаление участника (ADR-0009 §5, «без каскада»).
    """

    __tablename__ = "workspace_members"

    id: Mapped[int] = mapped_column(Integer, Identity(always=False), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default="owner")
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="workspace_members_uq"),
        CheckConstraint("role IN ('owner','member')", name="workspace_members_role_chk"),
    )
