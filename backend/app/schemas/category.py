from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CategoryKind = Literal["expense", "income", "both"]


class CategoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    kind: CategoryKind
    icon: str | None = Field(default=None, max_length=64)
    # parent_id — опциональная подкатегория. Глубина 2: parent сам не может
    # иметь parent (enforce в _validate_parent_ref, БД не гарантирует).
    parent_id: int | None = None


# parent_id намеренно отсутствует — move между родителями = другая
# семантическая категория, пересоздать.
class CategoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    icon: str | None = Field(default=None, max_length=64)
    archived_at: datetime | None = None


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int | None  # None = системная (глобальная)
    parent_id: int | None
    name: str
    kind: str
    icon: str | None
    archived_at: datetime | None
    created_at: datetime
