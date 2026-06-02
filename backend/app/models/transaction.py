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


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    # Аудит «кто создал» (заполняется с Phase 7); SET NULL — переживает purge юзера.
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(
        CHAR(3), nullable=False, server_default="RUB"
    )
    from_account_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("accounts.id", ondelete="RESTRICT")
    )
    to_account_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("accounts.id", ondelete="RESTRICT")
    )
    category_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("categories.id", ondelete="RESTRICT")
    )
    receipt_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("receipts.id")
    )
    # Источник tx — план + конкретное вхождение. Обе колонки NULL для tx,
    # созданных не из плана. Unique partial index `transactions_planned_uq`
    # ниже отбивает двойной confirm одного вхождения (ADR-0008).
    # C13-1: ondelete='RESTRICT' (после миграции 0005 шаг 9) — симметрично
    # MF11-2 (DELETE tx с planned_operation_id IS NOT NULL → 409). DELETE
    # plan с confirmed tx → IntegrityError → 409 в delete_planned. Юзер
    # вынужден архивировать, не удалять — consistency с history-immutable.
    planned_operation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("planned_operations.id", ondelete="RESTRICT")
    )
    occurrence_date: Mapped[date | None] = mapped_column(Date)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('expense','income','transfer','adjustment')",
            name="transactions_kind_chk",
        ),
        CheckConstraint("amount_minor > 0", name="transactions_amount_chk"),
        CheckConstraint("currency = 'RUB'", name="transactions_currency_chk"),
        # XOR-логика kind ↔ FK. Adjustment имеет ровно один из from/to (не оба).
        CheckConstraint(
            """
            (kind='expense'    AND from_account_id IS NOT NULL AND to_account_id IS NULL     AND category_id IS NOT NULL) OR
            (kind='income'     AND from_account_id IS NULL     AND to_account_id IS NOT NULL AND category_id IS NOT NULL) OR
            (kind='transfer'   AND from_account_id IS NOT NULL AND to_account_id IS NOT NULL AND from_account_id <> to_account_id AND category_id IS NULL) OR
            (kind='adjustment'
               AND ( (from_account_id IS NOT NULL AND to_account_id IS NULL)
                  OR (from_account_id IS NULL     AND to_account_id IS NOT NULL) )
               AND category_id IS NOT NULL)
            """,
            name="transactions_kind_fields_chk",
        ),
        Index(
            "transactions_ws_occurred_idx",
            "workspace_id",
            "occurred_at",
            postgresql_using="btree",
        ),
        Index(
            "transactions_from_acc_idx",
            "from_account_id",
            postgresql_where="from_account_id IS NOT NULL",
        ),
        Index(
            "transactions_to_acc_idx",
            "to_account_id",
            postgresql_where="to_account_id IS NOT NULL",
        ),
        Index(
            "transactions_receipt_idx",
            "receipt_id",
            postgresql_where="receipt_id IS NOT NULL",
        ),
        # Идемпотентность confirm плана: одно вхождение → одна tx (ADR-0008).
        # Partial WHERE отрезает все non-plan tx (где обе колонки NULL) от
        # индекса — Postgres 16 default NULLS DISTINCT тоже их пропустил бы,
        # но partial делает invariant независимым от глобального флага.
        Index(
            "transactions_planned_uq",
            "planned_operation_id",
            "occurrence_date",
            unique=True,
            postgresql_where="planned_operation_id IS NOT NULL",
        ),
    )
