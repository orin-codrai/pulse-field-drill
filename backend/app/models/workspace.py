from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Identity, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Workspace(Base):
    """Scope владения данными. Юзер «работает от лица» выбранного workspace
    (personal или shared). Все владеемые таблицы键ятся на workspace_id.
    См. ADR-0009.
    """

    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(Integer, Identity(always=False), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # 'personal' — личный (ровно 1 участник); 'shared' — совместный (v1: ≤2).
    # Инвариант, не label: invite-эндпоинт разрешён только на 'shared'.
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("kind IN ('personal','shared')", name="workspaces_kind_chk"),
    )
