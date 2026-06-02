from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, Identity(always=False), primary_key=True)
    # workspace_id NULL = системная (глобальная) категория. Единственный источник
    # истины «системности». User-категории键ятся на свой workspace.
    workspace_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("workspaces.id", ondelete="RESTRICT")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    icon: Mapped[str | None] = mapped_column(Text)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('expense','income','both')", name="categories_kind_chk"
        ),
        # `categories_ws_name_uq` — partial unique index с COALESCE(workspace_id, 0)
        # на (COALESCE(workspace_id, 0), name) WHERE archived_at IS NULL — создаётся
        # руками в миграции (autogenerate не поддерживает expression indexes).
    )
