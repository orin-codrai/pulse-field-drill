from datetime import datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Receipt(Base):
    __tablename__ = "receipts"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="uploaded"
    )
    parsed_total_minor: Mapped[int | None] = mapped_column(BigInteger)
    parsed_currency: Mapped[str | None] = mapped_column(CHAR(3))
    parsed_merchant: Mapped[str | None] = mapped_column(Text)
    parsed_occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parsed_raw: Mapped[Any | None] = mapped_column(JSONB().with_variant(JSON, "sqlite"))
    parse_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('uploaded','parsing','parsed','failed','rejected')",
            name="receipts_status_chk",
        ),
        CheckConstraint(
            "parsed_currency IS NULL OR parsed_currency = 'RUB'",
            name="receipts_parsed_currency_chk",
        ),
        Index("receipts_user_created_idx", "user_id", "created_at"),
        Index(
            "receipts_processing_idx",
            "status",
            postgresql_where="status IN ('uploaded','parsing')",
        ),
    )
