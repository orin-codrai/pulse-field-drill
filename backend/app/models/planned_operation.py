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


class PlannedOperation(Base):
    """Запланированная операция (income/expense) с рекуррентностью.

    completed_cycles — единственный источник истины «сколько вхождений уже
    подтверждено»; next_occurrence НЕ хранится, выводится через
    nth_occurrence(first_date, recurrence, completed_cycles). См. ADR-0008.

    Глубина дерева подкатегорий БД не гарантирует (бд CHECK не видит другую
    строку) — enforce в _validate_parent_ref на уровне роутера.
    """

    __tablename__ = "planned_operations"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(
        CHAR(3), nullable=False, server_default="RUB"
    )
    # MF10-1: NOT NULL для всех kind. transactions_kind_fields_chk требует
    # category для income И expense; если бы план разрешил income без
    # категории, confirm крашнулся бы на CHECK XOR транзакций → 500.
    category_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("categories.id", ondelete="RESTRICT"), nullable=False
    )
    account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    first_date: Mapped[date] = mapped_column(Date, nullable=False)
    recurrence: Mapped[str] = mapped_column(Text, nullable=False)
    total_cycles: Mapped[int | None] = mapped_column(Integer)
    completed_cycles: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="planned"
    )
    note: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "kind IN ('income','expense')", name="planned_kind_chk"
        ),
        CheckConstraint("amount_minor > 0", name="planned_amount_chk"),
        CheckConstraint("currency = 'RUB'", name="planned_currency_chk"),
        CheckConstraint(
            "recurrence IN ('once','week','month','year')",
            name="planned_recurrence_chk",
        ),
        CheckConstraint(
            "status IN ('planned','paused','done')", name="planned_status_chk"
        ),
        CheckConstraint("completed_cycles >= 0", name="planned_completed_chk"),
        CheckConstraint(
            "total_cycles IS NULL OR total_cycles >= 1", name="planned_total_chk"
        ),
        CheckConstraint(
            "total_cycles IS NULL OR completed_cycles <= total_cycles",
            name="planned_cycles_bounds_chk",
        ),
        # 'once' не имеет повторов → total_cycles бессмыслен (используем NULL).
        CheckConstraint(
            "(recurrence = 'once' AND total_cycles IS NULL) OR recurrence <> 'once'",
            name="planned_once_no_total_chk",
        ),
        Index(
            "planned_ws_status_idx",
            "workspace_id",
            "status",
            postgresql_where="archived_at IS NULL",
        ),
    )
