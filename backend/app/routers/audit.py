"""Audit log endpoint — `GET /api/audit` для UI «История изменений» в shared.

В personal возвращает пустой список (200, не 403) — фронт скрывает раздел
через `currentWorkspace.kind === 'personal'`; пустой ответ стабильнее.

actor_display_name: LEFT JOIN на users (live display_name); fallback на
snapshot_json.actor_name_snapshot если actor_user_id NULL (юзер hard-purged).
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_workspace
from app.db.session import get_session
from app.models import AuditLog, User, Workspace
from app.schemas.audit import AuditEntryOut

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=list[AuditEntryOut])
async def list_audit(
    ws: Workspace = Depends(current_workspace),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[AuditEntryOut]:
    if ws.kind != "shared":
        return []
    rows = (
        await session.execute(
            select(AuditLog, User.display_name, User.first_name)
            .outerjoin(User, User.id == AuditLog.actor_user_id)
            .where(AuditLog.workspace_id == ws.id)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(limit)
        )
    ).all()

    out: list[AuditEntryOut] = []
    for entry, display_name, first_name in rows:
        # actor_display_name resolve order:
        # 1. live display_name (если зарегистрирован, не purged).
        # 2. live first_name (TG profile fallback).
        # 3. snapshot_json.actor_name_snapshot (purged юзер — frozen из past).
        resolved = (
            display_name
            or first_name
            or entry.snapshot_json.get("actor_name_snapshot")
        )
        out.append(
            AuditEntryOut(
                id=entry.id,
                actor_user_id=entry.actor_user_id,
                actor_display_name=resolved,
                entity_type=entry.entity_type,
                entity_id=entry.entity_id,
                action=entry.action,
                created_at=entry.created_at,
            )
        )
    return out
