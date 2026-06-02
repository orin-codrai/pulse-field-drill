from datetime import date, datetime

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Envelope(Base):
    """Конверт (pay-yourself-first). Виртуальный срез общего баланса
    workspace; деньги физически НЕ двигаются между счетами. См. ADR-0007.

    Мигрирует из `goals` (миграция 0005): +percent, target nullable,
    drop linked_account_id. UniqueConstraint(id, workspace_id) — target
    композитного FK с envelope_entries (защита от drift денормализации
    workspace_id на entries).
    """

    __tablename__ = "envelopes"

    id: Mapped[int] = mapped_column(Integer, Identity(always=False), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # NULL = ручной конверт (без авто-скима); 1..100 = доля каждого дохода.
    percent: Mapped[int | None] = mapped_column(Integer)
    target_amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(
        CHAR(3), nullable=False, server_default="RUB"
    )
    target_date: Mapped[date | None] = mapped_column(Date)
    icon: Mapped[str | None] = mapped_column(Text)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "percent IS NULL OR (percent > 0 AND percent <= 100)",
            name="envelopes_percent_chk",
        ),
        CheckConstraint(
            "target_amount_minor IS NULL OR target_amount_minor > 0",
            name="envelopes_target_chk",
        ),
        CheckConstraint("currency = 'RUB'", name="envelopes_currency_chk"),
        Index(
            "envelopes_ws_active_idx",
            "workspace_id",
            postgresql_where="archived_at IS NULL",
        ),
        # Target для композитного FK envelope_entries → (envelopes.id,
        # envelopes.workspace_id). Постгрес требует unique constraint на
        # обе колонки явно; PK id один сам по себе недостаточен для
        # композитного FK (B1 pass 6, PIN-E pass 11).
        UniqueConstraint("id", "workspace_id", name="envelopes_id_ws_uq"),
    )
