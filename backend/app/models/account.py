from datetime import datetime

from sqlalchemy import (
    CHAR,
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


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, Identity(always=False), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    # Аудит «кто создал» (заполняется с Phase 7); SET NULL — переживает purge юзера.
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(
        CHAR(3), nullable=False, server_default="RUB"
    )
    initial_balance_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    icon: Mapped[str | None] = mapped_column(Text)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "type IN ('card','cash','savings','debt','credit')",
            name="accounts_type_chk",
        ),
        CheckConstraint("currency = 'RUB'", name="accounts_currency_chk"),
        Index(
            "accounts_ws_active_idx",
            "workspace_id",
            postgresql_where="archived_at IS NULL",
        ),
        Index(
            "accounts_ws_name_uq",
            "workspace_id",
            "name",
            unique=True,
            postgresql_where="archived_at IS NULL",
        ),
    )
