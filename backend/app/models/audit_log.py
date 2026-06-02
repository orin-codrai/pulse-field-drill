from datetime import datetime
from typing import Any

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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuditLog(Base):
    """Журнал «кто/что/когда». v1 покрывает transactions + accounts (ADR-0009 §6).

    `actor_user_id ondelete='SET NULL'`: hard-purge юзера не каскадно сносит
    audit (история переживает); UI показывает «бывший участник» через
    `snapshot_json.actor_name_snapshot` fallback.

    `workspace_id ondelete='RESTRICT'`: audit = история, не каскадно
    зависимая от workspace. Hard-purge ОБЯЗАН удалить audit явно ПЕРЕД
    workspace (`services/purge.py` шаг 5); RESTRICT страхует от случайного
    DELETE workspace мимо purge-сервиса.

    `entity_id` НЕ FK — entity может быть hard-purged (transactions
    удаляются по workspace_id IN personal_ws); audit живёт через
    snapshot_json. BigInteger вмещает обе колонки (transactions.id =
    BigInteger, accounts.id = Integer).
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False), primary_key=True
    )
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "entity_type IN ('transaction','account')",
            name="audit_log_entity_type_chk",
        ),
        CheckConstraint(
            "action IN ('create','update','delete')",
            name="audit_log_action_chk",
        ),
        # Основной запрос UI: история workspace по убыванию даты.
        Index("audit_log_ws_created_idx", "workspace_id", "created_at"),
        # Точечный поиск истории конкретной сущности (для будущего entity-history view).
        Index(
            "audit_log_entity_idx",
            "workspace_id",
            "entity_type",
            "entity_id",
        ),
    )
