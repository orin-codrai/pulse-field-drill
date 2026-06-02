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
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Goal(Base):
    __tablename__ = "goals"

    id: Mapped[int] = mapped_column(Integer, Identity(always=False), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    target_amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(
        CHAR(3), nullable=False, server_default="RUB"
    )
    target_date: Mapped[date | None] = mapped_column(Date)
    linked_account_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("accounts.id")
    )
    icon: Mapped[str | None] = mapped_column(Text)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("target_amount_minor > 0", name="goals_target_chk"),
        CheckConstraint("currency = 'RUB'", name="goals_currency_chk"),
        Index(
            "goals_ws_active_idx",
            "workspace_id",
            postgresql_where="archived_at IS NULL",
        ),
    )
