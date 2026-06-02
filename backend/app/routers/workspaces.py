from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.db.session import get_session
from app.models import User, Workspace, WorkspaceMember
from app.schemas.workspace import WorkspaceOut, WorkspaceSwitch

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get("", response_model=list[WorkspaceOut])
async def list_workspaces(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[Workspace]:
    """Workspace'ы, в которых юзер состоит (personal + shared)."""
    stmt = (
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id)
        .order_by(Workspace.id)
    )
    return list((await session.execute(stmt)).scalars().all())


@router.patch("/active", response_model=WorkspaceOut)
async def switch_active_workspace(
    body: WorkspaceSwitch,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Workspace:
    """Сменить активный workspace. Membership проверяется ДО записи (ADR-0009 §4,
    must-fix #8) — иначе юзер выставил бы чужой workspace и прошёл бы фильтры.
    """
    member = await session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == body.workspace_id,
            WorkspaceMember.user_id == user.id,
        )
    )
    if member is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "not a member of that workspace"
        )
    user.active_workspace_id = body.workspace_id
    await session.commit()
    ws = await session.get(Workspace, body.workspace_id)
    return ws
