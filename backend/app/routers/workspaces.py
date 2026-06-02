from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.db.session import get_session
from app.models import User, Workspace, WorkspaceMember
from app.schemas.workspace import WorkspaceCreate, WorkspaceOut, WorkspaceSwitch

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


@router.post("", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
async def create_shared_workspace(
    body: WorkspaceCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Workspace:
    """Создать shared workspace. Юзер автоматически добавляется как owner.

    Personal workspace создаётся только через provisioning
    (`ensure_user_provisioned`). Этот endpoint ВСЕГДА kind='shared' —
    нет пути юзеру создать второй personal.

    Registration enforce: endpoint должен быть behind `registered_user`
    в 7.D. Сейчас (до 7.D) использует current_user; будет обновлён
    одновременно с deploy auth/deps активного_user/registered_user.
    """
    ws = Workspace(name=body.name, kind="shared")
    session.add(ws)
    await session.flush()  # для ws.id
    session.add(
        WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner")
    )
    await session.commit()
    await session.refresh(ws)
    return ws


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
