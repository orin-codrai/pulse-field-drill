"""Конверты — CRUD + леджер entries. См. ADR-0007.

`reserved_minor` per envelope считается **через query-filter**
`WHERE archived_at IS NULL` (B2): архивный конверт исключается из
суммы, un-archive восстанавливает резерв без правки entries-истории.

`/entries` запрос — БЕЗ join на envelopes (C13-3): денормализация
workspace_id (MF2) + композитный FK (B1) обеспечивают эквивалентность
с join-вариантом без лишнего scan.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user, current_workspace
from app.db.session import get_session
from app.models import Envelope, EnvelopeEntry, User, Workspace
from app.schemas.envelope import (
    EnvelopeCreate,
    EnvelopeEntryCreate,
    EnvelopeEntryOut,
    EnvelopeOut,
    EnvelopeUpdate,
)

router = APIRouter(prefix="/envelopes", tags=["envelopes"])


async def _reserved_per_envelope(
    session: AsyncSession, workspace_id: int
) -> dict[int, int]:
    """Σ amount_minor по envelope_id для всех активных конвертов workspace.
    Возвращает {envelope_id: reserved_minor}. Archived envelopes отсутствуют
    в выдаче (фильтр по Envelope, не по EnvelopeEntry — leдgers immutable)."""
    rows = (await session.execute(
        select(
            EnvelopeEntry.envelope_id,
            func.coalesce(func.sum(EnvelopeEntry.amount_minor), 0).label("reserved"),
        )
        .join(Envelope, Envelope.id == EnvelopeEntry.envelope_id)
        .where(
            Envelope.workspace_id == workspace_id,
            Envelope.archived_at.is_(None),
        )
        .group_by(EnvelopeEntry.envelope_id)
    )).all()
    return {r.envelope_id: int(r.reserved) for r in rows}


def _to_out(env: Envelope, reserved: int) -> EnvelopeOut:
    return EnvelopeOut(
        id=env.id,
        workspace_id=env.workspace_id,
        name=env.name,
        percent=env.percent,
        target_amount_minor=env.target_amount_minor,
        currency=env.currency,
        target_date=env.target_date,
        icon=env.icon,
        archived_at=env.archived_at,
        created_at=env.created_at,
        reserved_minor=reserved,
    )


@router.get("", response_model=list[EnvelopeOut])
async def list_envelopes(
    ws: Workspace = Depends(current_workspace),
    session: AsyncSession = Depends(get_session),
    include_archived: bool = Query(default=False),
) -> list[EnvelopeOut]:
    """Список конвертов workspace. По умолчанию активные;
    `?include_archived=true` показывает архивные с `reserved_minor=0`
    (леджер цел, но в активном балансе не учитывается)."""
    stmt = select(Envelope).where(Envelope.workspace_id == ws.id)
    if not include_archived:
        stmt = stmt.where(Envelope.archived_at.is_(None))
    envs = list((await session.execute(stmt.order_by(Envelope.id))).scalars().all())

    reserved = await _reserved_per_envelope(session, ws.id)
    return [_to_out(e, reserved.get(e.id, 0)) for e in envs]


@router.post("", response_model=EnvelopeOut, status_code=status.HTTP_201_CREATED)
async def create_envelope(
    body: EnvelopeCreate,
    ws: Workspace = Depends(current_workspace),
    session: AsyncSession = Depends(get_session),
) -> EnvelopeOut:
    env = Envelope(workspace_id=ws.id, **body.model_dump())
    session.add(env)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        msg = str(e.orig)
        if "envelopes_" in msg and "_chk" in msg:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "envelope violates a CHECK constraint",
            ) from e
        raise
    await session.refresh(env)
    return _to_out(env, 0)


@router.patch("/{envelope_id}", response_model=EnvelopeOut)
async def update_envelope(
    envelope_id: int,
    body: EnvelopeUpdate,
    ws: Workspace = Depends(current_workspace),
    session: AsyncSession = Depends(get_session),
) -> EnvelopeOut:
    env = await session.scalar(
        select(Envelope).where(
            Envelope.id == envelope_id, Envelope.workspace_id == ws.id
        )
    )
    if env is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "envelope not found")

    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(env, field, value)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        msg = str(e.orig)
        if "envelopes_" in msg and "_chk" in msg:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "envelope violates a CHECK constraint",
            ) from e
        raise
    await session.refresh(env)

    # После update пересчитываем reserved для актуального ответа.
    reserved = await _reserved_per_envelope(session, ws.id)
    return _to_out(env, reserved.get(env.id, 0))


@router.delete("/{envelope_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_envelope(
    envelope_id: int,
    ws: Workspace = Depends(current_workspace),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Запрет если есть entries — иммутабельная история, удаление
    исказило бы баланс. Архивация — допустимый способ скрыть конверт."""
    env = await session.scalar(
        select(Envelope).where(
            Envelope.id == envelope_id, Envelope.workspace_id == ws.id
        )
    )
    if env is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "envelope not found")

    n_entries = await session.scalar(
        select(func.count(EnvelopeEntry.id)).where(
            EnvelopeEntry.envelope_id == env.id,
            EnvelopeEntry.workspace_id == ws.id,
        )
    )
    if n_entries:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "cannot delete envelope with ledger entries; archive instead",
        )

    await session.delete(env)
    await session.commit()


# ─── Entries ────────────────────────────────────────────────────────────────


@router.get(
    "/{envelope_id}/entries", response_model=list[EnvelopeEntryOut]
)
async def list_entries(
    envelope_id: int,
    ws: Workspace = Depends(current_workspace),
    session: AsyncSession = Depends(get_session),
) -> list[EnvelopeEntry]:
    """C13-3: запрос БЕЗ join на envelopes — денормализация workspace_id
    (MF2) + композитный FK (B1) дают эквивалентный фильтр без scan.

    Сначала проверяем что envelope существует и принадлежит workspace
    (отдельный SELECT по envelopes), потом отдаём entries."""
    env_exists = await session.scalar(
        select(Envelope.id).where(
            Envelope.id == envelope_id, Envelope.workspace_id == ws.id
        )
    )
    if env_exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "envelope not found")

    stmt = (
        select(EnvelopeEntry)
        .where(
            EnvelopeEntry.envelope_id == envelope_id,
            EnvelopeEntry.workspace_id == ws.id,
        )
        .order_by(EnvelopeEntry.created_at.desc(), EnvelopeEntry.id.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


@router.post(
    "/{envelope_id}/entries",
    response_model=EnvelopeEntryOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_entry(
    envelope_id: int,
    body: EnvelopeEntryCreate,
    ws: Workspace = Depends(current_workspace),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> EnvelopeEntry:
    """Manual / withdraw entry. amount_minor>0 в payload; для withdraw
    сохраняется отрицательное (PIN-C). created_by_user_id из current_user
    server-side (MF1, не из body)."""
    env = await session.scalar(
        select(Envelope).where(
            Envelope.id == envelope_id, Envelope.workspace_id == ws.id
        )
    )
    if env is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "envelope not found")
    if env.archived_at is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "cannot add entries to archived envelope"
        )

    amount = body.amount_minor if body.kind == "manual" else -body.amount_minor
    entry = EnvelopeEntry(
        envelope_id=env.id,
        workspace_id=ws.id,
        amount_minor=amount,
        kind=body.kind,
        source_transaction_id=None,
        created_by_user_id=user.id,
    )
    session.add(entry)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        msg = str(e.orig)
        if "envelope_entries_" in msg and ("_chk" in msg or "_fkey" in msg):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "envelope entry constraint violated",
            ) from e
        raise
    await session.refresh(entry)
    return entry
