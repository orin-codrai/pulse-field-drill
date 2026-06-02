"""Workspace invites — create / list / revoke / preview / accept.

Endpoints:
- POST /api/workspaces/{id}/invites      — create (owner, только shared)
- GET  /api/workspaces/{id}/invites      — список
- DELETE /api/workspaces/{id}/invites/{invite_id}   — revoke pending
- GET  /api/invites/{token}              — preview (любой авторизованный)
- POST /api/invites/{token}/accept       — accept + auto-switch active_workspace

Auth: все защищены `current_user` (P7.D добавит `registered_user` на create/
accept). Cross-workspace через URL — проверяем `workspace_id IN URL ==
current_workspace.id` (защита от escalation через переключение active_workspace
между чтением и POST'ом).
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user, current_workspace, registered_user
from app.db.session import get_session
from app.models import User, Workspace, WorkspaceInvite
from app.schemas.invite import InviteOut, InvitePreview
from app.services.invites import (
    InviteError,
    accept_invite,
    create_invite,
)

router = APIRouter(tags=["invites"])


# ─── Workspace-scoped: create/list/revoke ───────────────────────────────────


@router.post(
    "/workspaces/{workspace_id}/invites",
    response_model=InviteOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace_invite(
    workspace_id: int,
    ws: Workspace = Depends(current_workspace),
    user: User = Depends(registered_user),  # PIN-E
    session: AsyncSession = Depends(get_session),
) -> WorkspaceInvite:
    if workspace_id != ws.id:
        # URL-mismatch: юзер мог переключить active_workspace и заслать POST с
        # другим id (ошибка фронта ИЛИ попытка escalation). Не разрешаем.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "workspace_id in URL does not match active workspace",
        )
    if ws.kind != "shared":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "cannot create invite for personal workspace",
        )
    try:
        invite = await create_invite(session, workspace=ws, actor=user)
        await session.commit()
    except InviteError as e:
        await session.rollback()
        raise HTTPException(e.http_status, e.message) from e
    await session.refresh(invite)
    return invite


@router.get(
    "/workspaces/{workspace_id}/invites",
    response_model=list[InviteOut],
)
async def list_workspace_invites(
    workspace_id: int,
    ws: Workspace = Depends(current_workspace),
    session: AsyncSession = Depends(get_session),
) -> list[WorkspaceInvite]:
    if workspace_id != ws.id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "workspace_id in URL does not match active workspace",
        )
    stmt = (
        select(WorkspaceInvite)
        .where(WorkspaceInvite.workspace_id == ws.id)
        .order_by(
            WorkspaceInvite.created_at.desc(), WorkspaceInvite.id.desc()
        )
    )
    return list((await session.execute(stmt)).scalars().all())


@router.delete(
    "/workspaces/{workspace_id}/invites/{invite_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_workspace_invite(
    workspace_id: int,
    invite_id: int,
    ws: Workspace = Depends(current_workspace),
    session: AsyncSession = Depends(get_session),
) -> None:
    if workspace_id != ws.id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "workspace_id in URL does not match active workspace",
        )
    invite = await session.scalar(
        select(WorkspaceInvite).where(
            WorkspaceInvite.id == invite_id,
            WorkspaceInvite.workspace_id == ws.id,
        )
    )
    if invite is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "invite not found")
    if invite.status != "pending":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"cannot revoke invite in status {invite.status}",
        )
    invite.status = "revoked"
    await session.commit()


# ─── Token-scoped: preview / accept (без workspace-membership) ─────────────


@router.get("/invites/{token}", response_model=InvitePreview)
async def get_invite_preview(
    token: str,
    user: User = Depends(current_user),  # requires auth, не requires membership
    session: AsyncSession = Depends(get_session),
) -> InvitePreview:
    """Preview перед accept screen. Не требует membership в workspace —
    token = авторизация для этого endpoint'a."""
    invite = await session.scalar(
        select(WorkspaceInvite).where(WorkspaceInvite.token == token)
    )
    if invite is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "invite not found")
    workspace = await session.get(Workspace, invite.workspace_id)
    if workspace is None:
        raise HTTPException(
            status.HTTP_410_GONE, "workspace no longer exists"
        )
    inviter = (
        await session.get(User, invite.created_by_user_id)
        if invite.created_by_user_id is not None
        else None
    )
    inviter_name = (
        (inviter.display_name or inviter.first_name) if inviter else None
    )
    return InvitePreview(
        workspace_id=workspace.id,
        workspace_name=workspace.name,
        workspace_kind=workspace.kind,
        status=invite.status,
        expires_at=invite.expires_at,
        inviter_display_name=inviter_name,
    )


@router.post("/invites/{token}/accept", response_model=InviteOut)
async def accept_invite_endpoint(
    token: str,
    user: User = Depends(registered_user),  # PIN-E: имя видно партнёру в audit
    session: AsyncSession = Depends(get_session),
) -> WorkspaceInvite:
    try:
        workspace = await accept_invite(
            session, token=token, accepting_user=user
        )
        # PIN-B: auto-switch active_workspace_id (deep-link → юзер ожидает
        # попасть в shared сразу после accept).
        user.active_workspace_id = workspace.id
        await session.commit()
    except InviteError as e:
        await session.rollback()
        raise HTTPException(e.http_status, e.message) from e
    invite = await session.scalar(
        select(WorkspaceInvite).where(WorkspaceInvite.token == token)
    )
    return invite
