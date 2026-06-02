from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WorkspaceInvite(Base):
    """Приглашение в shared workspace через Telegram deep-link.

    Token: `secrets.token_urlsafe(32)` → ~43 ASCII (TG `startapp` лимит ~64).
    TTL: 7 дней (ADR-0009 §7). Cap shared ≤ 2 (членов) и shared ≤ 3 на юзера —
    enforced application-level в `services/invites.accept_invite` под
    `pg_advisory_xact_lock(user.id)` + `SELECT FOR UPDATE` на workspace.

    `workspace_id ondelete='CASCADE'`: hard-purge workspace удаляет invites
    вместе (не оставляет dangling token'ы).

    FK actor'ов `ondelete='SET NULL'`: purge юзера не блокируется invite-FK;
    UI показывает «бывший участник» через snapshot fallback (см. AuditLog).
    """

    __tablename__ = "workspace_invites"

    id: Mapped[int] = mapped_column(Integer, Identity(always=False), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    token: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    accepted_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="pending"
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','accepted','revoked','expired')",
            name="workspace_invites_status_chk",
        ),
        # Terminal accepted требует обе колонки NOT NULL; pending/revoked/expired
        # требуют accepted_at NULL (accepted_by_user_id может быть NULL и для
        # accepted после hard-purge — это SET NULL hatch, не нарушение CHECK).
        CheckConstraint(
            "(status = 'accepted' AND accepted_at IS NOT NULL) "
            "OR (status <> 'accepted' AND accepted_at IS NULL)",
            name="workspace_invites_accepted_consistency_chk",
        ),
        Index("workspace_invites_ws_idx", "workspace_id"),
    )
