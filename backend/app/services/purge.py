"""Hard-purge юзера: физическое удаление personal-данных после 30-дневного
soft-delete окна. v1 — manual CLI `python -m app.cli purge-deleted-users`
(cron-фреймворк backlog, юзеров двое).

Порядок удаления (ADR-0009 §8 + plan-reviewer pass 14 MF14-7):

    0. GUARD — raise ValueError если user.deleted_at IS NULL ИЛИ
       <PURGE_AFTER_DAYS дней с момента soft-delete. Защита от случайного
       вызова на живом или преждевременно-deleted юзере. `raise` (не assert)
       устойчиво к PYTHONOPTIMIZE=1.
    1. envelope_entries WHERE workspace_id IN personal_ws
    2. transactions WHERE workspace_id IN personal_ws
    3. planned_operations WHERE workspace_id IN personal_ws
    4. accounts/categories/budgets/envelopes/receipts WHERE workspace_id IN
       personal_ws
    5. audit_log WHERE workspace_id IN personal_ws (RESTRICT — явный шаг до workspace)
    6. workspace_invites WHERE workspace_id IN personal_ws (CASCADE сработает,
       но делаем явно для контроля)
    7. workspace_members WHERE user_id=user.id (включая остатки в shared)
    8. workspaces WHERE id IN personal_ws (только personal — shared НЕ трогаем)
    9. user

Shared workspace'ы НЕ удаляются (PIN-G: B продолжает использовать без A).
В shared FK на user'a:
- accounts/transactions/envelope_entries/planned_operations.created_by_user_id
  → SET NULL (FK ondelete);
- audit_log.actor_user_id → SET NULL (FK ondelete);
- workspace_invites.created_by_user_id/accepted_by_user_id → SET NULL.
Эти SET NULL сработают автоматически при DELETE user на шаге 9.
"""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Account,
    AuditLog,
    Budget,
    Category,
    Envelope,
    EnvelopeEntry,
    PlannedOperation,
    Receipt,
    Transaction,
    User,
    Workspace,
    WorkspaceInvite,
    WorkspaceMember,
)
from app.routers.me import PURGE_AFTER_DAYS


def _now_utc():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


async def hard_purge_user(session: AsyncSession, user: User) -> None:
    """Физически удалить юзера + personal-данные. См. порядок в docstring модуля.

    Guard (MF14-7): raise ValueError если deleted_at IS NULL ИЛИ < 30 дней.
    """
    if user.deleted_at is None:
        raise ValueError(
            f"hard_purge_user invoked on non-deleted user (id={user.id})"
        )
    age_days = (_now_utc() - user.deleted_at).days
    if age_days < PURGE_AFTER_DAYS:
        raise ValueError(
            f"hard_purge_user invoked before purge window "
            f"(age={age_days}d < {PURGE_AFTER_DAYS}d, user.id={user.id})"
        )

    # Personal workspace ID's юзера.
    personal_ws_ids = (await session.execute(
        select(Workspace.id)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(
            WorkspaceMember.user_id == user.id,
            Workspace.kind == "personal",
        )
    )).scalars().all()
    personal_ws_ids = list(personal_ws_ids)

    if personal_ws_ids:
        # 1. envelope_entries.
        await session.execute(
            delete(EnvelopeEntry).where(
                EnvelopeEntry.workspace_id.in_(personal_ws_ids)
            )
        )
        # 2. transactions.
        await session.execute(
            delete(Transaction).where(
                Transaction.workspace_id.in_(personal_ws_ids)
            )
        )
        # 3. planned_operations.
        await session.execute(
            delete(PlannedOperation).where(
                PlannedOperation.workspace_id.in_(personal_ws_ids)
            )
        )
        # 4. accounts / categories / budgets / envelopes / receipts.
        for model in (Account, Budget, Receipt, Envelope, Category):
            await session.execute(
                delete(model).where(
                    model.workspace_id.in_(personal_ws_ids)
                )
            )
        # 5. audit_log (RESTRICT — явный шаг ДО workspace).
        await session.execute(
            delete(AuditLog).where(
                AuditLog.workspace_id.in_(personal_ws_ids)
            )
        )
        # 6. workspace_invites (CASCADE сработает, делаем явно).
        await session.execute(
            delete(WorkspaceInvite).where(
                WorkspaceInvite.workspace_id.in_(personal_ws_ids)
            )
        )

    # 7. ALL workspace_members юзера (включая остатки в shared если они
    #    остались — хотя /me/delete должен был их удалить).
    await session.execute(
        delete(WorkspaceMember).where(WorkspaceMember.user_id == user.id)
    )

    # 8. Personal workspaces (только — shared не трогаем).
    if personal_ws_ids:
        await session.execute(
            delete(Workspace).where(Workspace.id.in_(personal_ws_ids))
        )

    # 9. user. FK SET NULL автоматом обработает actor_user_id /
    #    created_by_user_id в shared workspace'ах (audit, invite,
    #    accounts/tx/planned/envelope_entries).
    await session.delete(user)
    await session.commit()
