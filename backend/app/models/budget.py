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


class Budget(Base):
    __tablename__ = "budgets"

    id: Mapped[int] = mapped_column(Integer, Identity(always=False), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    category_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("categories.id"), nullable=False
    )
    period: Mapped[str] = mapped_column(Text, nullable=False)
    limit_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(
        CHAR(3), nullable=False, server_default="RUB"
    )
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date | None] = mapped_column(Date)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "period IN ('week','month','year')", name="budgets_period_chk"
        ),
        CheckConstraint("limit_minor > 0", name="budgets_limit_chk"),
        CheckConstraint("currency = 'RUB'", name="budgets_currency_chk"),
        CheckConstraint(
            "ends_on IS NULL OR ends_on > starts_on", name="budgets_dates_chk"
        ),
        Index(
            "budgets_active_uq",
            "user_id",
            "category_id",
            "period",
            unique=True,
            postgresql_where="archived_at IS NULL",
        ),
    )
