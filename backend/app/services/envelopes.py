"""Auto-skim для активных конвертов при подтверждении дохода. См. ADR-0007.

Вызывается из:
  - `routers/transactions.create_transaction` (POST tx с kind='income')
  - `routers/planned.confirm_planned` (когда op.kind == 'income')

Контракт: одна БД-транзакция с income tx; commit делает caller. Сервис
flush'ит tx (если ещё без id), читает активные конверты, вставляет
skim-entries в той же session.

`adjustment` с to_account_id растит баланс, но НЕ доход (MF4 pass 5).
Caller проверяет `tx.kind == 'income'` перед вызовом; сервис ставит
второй слой защиты через raise ValueError (MF7: assert оптимизируется
PYTHONOPTIMIZE=1).
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Envelope, EnvelopeEntry, Transaction


async def skim_on_income(
    session: AsyncSession,
    tx: Transaction,
    *,
    actor_user_id: int,
) -> list[EnvelopeEntry]:
    """Вставляет skim-entries для всех активных конвертов workspace.
    Возвращает созданные entries (для тестов и логирования).

    `floor(amount * pct / 100)` через целое деление: Σ скимов никогда
    не превысит доход.
    """
    if tx.kind != "income":
        raise ValueError(f"skim_on_income invoked on kind={tx.kind!r}")
    if tx.id is None:
        # FK source_transaction_id требует уже-flush'нутый tx.
        await session.flush()

    envelopes = (await session.execute(
        select(Envelope).where(
            Envelope.workspace_id == tx.workspace_id,
            Envelope.archived_at.is_(None),
            Envelope.percent.is_not(None),
        )
    )).scalars().all()

    entries: list[EnvelopeEntry] = []
    for env in envelopes:
        # amount * pct → int, // 100 → floor (для positive amount).
        skim = (tx.amount_minor * env.percent) // 100
        if skim == 0:
            continue  # маленький amount * процент → floor=0, лeджер чище
        entry = EnvelopeEntry(
            envelope_id=env.id,
            workspace_id=tx.workspace_id,
            amount_minor=skim,
            kind="skim",
            source_transaction_id=tx.id,
            created_by_user_id=actor_user_id,
        )
        session.add(entry)
        entries.append(entry)
    return entries
