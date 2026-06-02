from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class AuditEntryOut(BaseModel):
    """Карточка одной строки audit для UI «История изменений».

    `actor_display_name` уже резолвлен на сервере: live join на users.display_name
    (или first_name fallback), либо `snapshot_json.actor_name_snapshot` если
    actor_user_id IS NULL (юзер hard-purged → «бывший участник»).

    `actor_user_id` отдаётся для frontend сравнения с `me.internal_id`
    («вы»-маркер). NULL после hard-purge.
    """

    model_config = ConfigDict(from_attributes=True)
    id: int
    actor_user_id: int | None
    actor_display_name: str | None
    entity_type: Literal["transaction", "account"]
    entity_id: int
    action: Literal["create", "update", "delete"]
    created_at: datetime
