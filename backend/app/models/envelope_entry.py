from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EnvelopeEntry(Base):
    """Иммутабельная строка леджера конверта. См. ADR-0007.

    `workspace_id` денормализован (MF2 pass 5): изоляция не должна
    зависеть от join через envelopes — иначе «забыл guard на
    /entries-эндпоинте → cross-workspace утечка». Композитный FK
    `(envelope_id, workspace_id) → envelopes(id, workspace_id)` (B1
    pass 6) гарантирует, что entry.workspace_id == envelope.workspace_id
    — БД пресекает drift.

    `amount_minor` signed: skim/manual положительные, withdraw
    отрицательный. `reserved = Σ amount_minor` без хирургии знаков.

    `source_transaction_id ondelete='CASCADE'`: DELETE income tx →
    skim entries автоматически уходят, reserved синхронизируется с
    balance. Без этого reserved >= balance после tx-DELETE.
    """

    __tablename__ = "envelope_entries"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    envelope_id: Mapped[int] = mapped_column(Integer, nullable=False)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    source_transaction_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("transactions.id", ondelete="CASCADE")
    )
    # Server-set из current_user (MF1 pass 11). v1 не имеет system-paths,
    # тип `int` без Optional закрыт до появления backfill/CLI need.
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('skim','manual','withdraw')",
            name="envelope_entries_kind_chk",
        ),
        CheckConstraint(
            "(kind = 'withdraw' AND amount_minor < 0) "
            "OR (kind IN ('skim','manual') AND amount_minor > 0)",
            name="envelope_entries_sign_chk",
        ),
        # Композитный FK против дрейфа денормализации (B1).
        ForeignKeyConstraint(
            ["envelope_id", "workspace_id"],
            ["envelopes.id", "envelopes.workspace_id"],
            name="envelope_entries_envelope_fkey",
            ondelete="RESTRICT",
        ),
        Index(
            "envelope_entries_env_idx",
            "envelope_id",
            "workspace_id",
        ),
        Index(
            "envelope_entries_source_tx_idx",
            "source_transaction_id",
            postgresql_where="source_transaction_id IS NOT NULL",
        ),
    )
