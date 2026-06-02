"""Audit-log сервис. Вызывается из routers/transactions.py + routers/accounts.py
на create/update/delete. ADR-0009 §6.

Контракт:
- Одна БД-транзакция с основной мутацией (commit делает caller).
- actor_user_id из current_user (server-side, не из body).
- snapshot_json — after-state сущности на момент действия (для delete —
  before-state, потому что после session.delete() её нет).
- actor_name_snapshot внутри snapshot — fallback для UI после hard-purge юзера
  (FK SET NULL → actor_user_id IS NULL → JSON-snapshot подсказывает имя).
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, User


def _serialize_transaction(tx) -> dict[str, Any]:
    return {
        "id": tx.id,
        "kind": tx.kind,
        "amount_minor": tx.amount_minor,
        "currency": tx.currency,
        "from_account_id": tx.from_account_id,
        "to_account_id": tx.to_account_id,
        "category_id": tx.category_id,
        "occurred_at": tx.occurred_at.isoformat() if tx.occurred_at else None,
        "note": tx.note,
        "planned_operation_id": tx.planned_operation_id,
    }


def _serialize_account(acc) -> dict[str, Any]:
    return {
        "id": acc.id,
        "name": acc.name,
        "type": acc.type,
        "currency": acc.currency,
        "initial_balance_minor": acc.initial_balance_minor,
        "icon": acc.icon,
        "archived_at": acc.archived_at.isoformat() if acc.archived_at else None,
    }


_SERIALIZERS = {
    "transaction": _serialize_transaction,
    "account": _serialize_account,
}


async def log_action(
    session: AsyncSession,
    *,
    workspace_id: int,
    actor: User,
    entity_type: str,
    entity_id: int,
    action: str,
    entity,
) -> AuditLog:
    """Записать строку аудита. Caller передаёт уже-flushed сущность (entity.id
    есть) для create/update; для delete — передаёт сущность ПЕРЕД
    session.delete() чтобы поля были читаемы.

    Raises ValueError если entity_type не поддержан (defence-in-depth — CHECK
    в БД тоже отбивает, но raise здесь даёт точный source). Аналогично action.
    """
    serializer = _SERIALIZERS.get(entity_type)
    if serializer is None:
        raise ValueError(f"unknown entity_type for audit: {entity_type!r}")
    if action not in ("create", "update", "delete"):
        raise ValueError(f"unknown audit action: {action!r}")

    # Fallback chain: display_name (если зарегистрирован) → first_name из TG →
    # username → tg:<id>. Гарантирует не-NULL snapshot для UI «бывший участник».
    actor_name = (
        actor.display_name
        or actor.first_name
        or actor.username
        or f"tg:{actor.tg_id}"
    )
    snapshot = {
        "after": serializer(entity),
        "actor_name_snapshot": actor_name,
    }
    row = AuditLog(
        workspace_id=workspace_id,
        actor_user_id=actor.id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        snapshot_json=snapshot,
    )
    session.add(row)
    return row
