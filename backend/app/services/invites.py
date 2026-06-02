"""Workspace invite сервис: генерация token, accept-логика с race protection.

Race-protection (ADR-0009 §7 + plan-reviewer passes 14-16):
- Token: `secrets.token_urlsafe(32)` (~256 бит энтропии); retry 3× на UNIQUE
  collision (defensive).
- `accept_invite` берёт три lock'a в стабильном порядке:
    0. `pg_advisory_xact_lock(_ACCEPT_LOCK_NS ^ user.id)` — сериализует
       cross-workspace accept'ы одного юзера (для cap-shared-per-user race;
       MF15-1: lock на одном ws не сериализует accept'ы на разные ws одного юзера).
    1. `SELECT FROM workspaces WHERE id=... FOR UPDATE` — для cap-workspace
       race (cap=2 членов).
    2. `SELECT FROM workspace_invites WHERE token=... FOR UPDATE` — для
       race на одном invite (двойной accept того же token).
  Порядок 0→1→2 alphabetical-by-table (защита от deadlock с revoke / hard_purge).

Cap'ы (ADR-0009 §5 «v1: shared ≤ 2»):
- `SHARED_WORKSPACE_CAP=2`: членов в одном shared workspace.
- `SHARED_PER_USER_CAP=3`: shared workspaces на одного юзера (UX-защита
  workspace switcher'a в Меню от 100+ items).
"""

import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, Workspace, WorkspaceInvite, WorkspaceMember

INVITE_TTL_DAYS = 7
SHARED_WORKSPACE_CAP = 2
SHARED_PER_USER_CAP = 3
_MAX_TOKEN_RETRIES = 3

# Namespace для pg_advisory_xact_lock на accept (XOR с user.id).
# ASCII 'pulseac' → 0x70_75_6c_73_65_61_63 = 31.6e15, влезает в bigint (<9.2e18).
# Диапазон не пересекается с provisioning advisory_xact_lock(tg_id) (~1e10).
_ACCEPT_LOCK_NS = 0x70_75_6c_73_65_61_63


class InviteError(Exception):
    """Domain error для invite-сервиса. Маппится в HTTPException в роутере."""

    def __init__(self, code: str, message: str, http_status: int = 409):
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(message)


async def create_invite(
    session: AsyncSession, *, workspace: Workspace, actor: User
) -> WorkspaceInvite:
    """Создать pending invite на shared workspace. Caller гарантирует
    membership актора (current_workspace его проверяет в роутере).

    MF14-2 — defence-in-depth: роутер 403'ит на non-shared, но если сервис
    вызовут напрямую (CLI/будущий рефактор) — invite на personal CASCADE-
    снёс бы audit-цепочку через workspace_invites ondelete=CASCADE.
    """
    if workspace.kind != "shared":
        raise ValueError(
            f"create_invite on non-shared workspace: kind={workspace.kind!r}"
        )

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=INVITE_TTL_DAYS)

    # Retry на UNIQUE collision на token — теоретически невозможно (256 бит),
    # defensive.
    for _ in range(_MAX_TOKEN_RETRIES):
        token = secrets.token_urlsafe(32)
        invite = WorkspaceInvite(
            workspace_id=workspace.id,
            token=token,
            created_by_user_id=actor.id,
            status="pending",
            expires_at=expires_at,
        )
        session.add(invite)
        try:
            await session.flush()
            return invite
        except IntegrityError as e:
            await session.rollback()
            if "workspace_invites_token_key" in str(e.orig):
                continue
            raise
    raise InviteError(
        "token_generation_failed", "could not generate unique token", 500
    )


async def accept_invite(
    session: AsyncSession, *, token: str, accepting_user: User
) -> Workspace:
    """Accept invite. Возвращает workspace, в который добавлен membership.

    Race-protection: см. module docstring.
    """
    # ШАГ 0: advisory_xact_lock на user.id — сериализует все cross-workspace
    # accept'ы одного юзера (для cap_shared_per_user race). Lock автоматом
    # снимается на commit/rollback (xact_lock).
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": _ACCEPT_LOCK_NS ^ accepting_user.id},
    )

    # ШАГ 1: read token → workspace_id (без lock).
    ws_id = await session.scalar(
        select(WorkspaceInvite.workspace_id).where(
            WorkspaceInvite.token == token
        )
    )
    if ws_id is None:
        raise InviteError("not_found", "invite not found", 404)

    # ШАГ 2: lock workspace (cap_workspace race на cap=2 членов).
    await session.execute(
        select(Workspace.id).where(Workspace.id == ws_id).with_for_update()
    )

    # ШАГ 3: lock invite + re-read под workspace lock.
    invite = await session.scalar(
        select(WorkspaceInvite)
        .where(WorkspaceInvite.token == token)
        .with_for_update()
    )
    # invite не None — иначе шаг 1 уже бы 404'нул.

    now = datetime.now(timezone.utc)

    # Lazy expire: помечаем и 410.
    if invite.status == "pending" and invite.expires_at < now:
        invite.status = "expired"
        await session.flush()
        raise InviteError("expired", "invite expired", 410)

    if invite.status != "pending":
        raise InviteError(
            "not_pending",
            f"invite is {invite.status}, cannot accept",
            409,
        )

    workspace = await session.get(Workspace, ws_id)
    if workspace is None:
        # workspace удалён между шагом 1 и lock — защита от ручного DDL.
        raise InviteError("workspace_gone", "workspace no longer exists", 410)

    # Already member?
    existing_member = await session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.user_id == accepting_user.id,
        )
    )
    if existing_member is not None:
        raise InviteError(
            "already_member",
            "you are already a member of this workspace",
            409,
        )

    # Cap-2 (членов в workspace) под workspace lock.
    member_count = await session.scalar(
        select(func.count(WorkspaceMember.id)).where(
            WorkspaceMember.workspace_id == workspace.id
        )
    )
    if member_count >= SHARED_WORKSPACE_CAP:
        raise InviteError(
            "cap_reached",
            f"workspace is at capacity ({SHARED_WORKSPACE_CAP} members)",
            409,
        )

    # Cap-3 (shared workspace на юзера) под advisory lock на user.id.
    user_shared_count = await session.scalar(
        select(func.count(WorkspaceMember.id))
        .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
        .where(
            WorkspaceMember.user_id == accepting_user.id,
            Workspace.kind == "shared",
        )
    )
    if user_shared_count >= SHARED_PER_USER_CAP:
        raise InviteError(
            "user_cap_reached",
            f"you are at capacity ({SHARED_PER_USER_CAP} shared workspaces)",
            409,
        )

    # Atomic insert membership + mark invite accepted.
    # C17-2: role='member' хардкод; P7 ролей не использует, owner-роль
    # исторична (provisioning'овский personal owner). Backlog (3+ участников /
    # admin): при PIN-P scenario (inviter soft-deleted → A's owner-row снят
    # → B становится единственным членом БЕЗ owner'a) потребуется path
    # «promote to owner» при single-member dangling.
    session.add(
        WorkspaceMember(
            workspace_id=workspace.id,
            user_id=accepting_user.id,
            role="member",
        )
    )
    invite.status = "accepted"
    invite.accepted_by_user_id = accepting_user.id
    invite.accepted_at = now

    return workspace
