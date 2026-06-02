"""Tests for services/envelopes.py:skim_on_income.

Чистые unit'ы поверх в-памяти ORM объектов + commit'ed setup, без
HTTP layer. Покрывают: одиночный конверт, несколько Σ, archived/manual
исключаются, floor=0 skip, raise на non-income, source_transaction_id
проставляется.
"""

from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, Category, Envelope, EnvelopeEntry, Transaction
from app.schemas.user import TelegramUser
from app.services.envelopes import skim_on_income
from app.services.user_provisioning import ensure_user_provisioned


@pytest_asyncio.fixture
async def setup(db_session: AsyncSession):
    """Workspace с одним юзером + 1 expense-категория + 1 income-категория +
    1 account. Возвращает dict-handle для тестов."""
    user = await ensure_user_provisioned(
        db_session, TelegramUser(id=91001, first_name="Skim")
    )
    await db_session.commit()
    ws_id = user.active_workspace_id
    acc = await db_session.scalar(
        select(Account).where(Account.workspace_id == ws_id).limit(1)
    )
    income_cat = await db_session.scalar(
        select(Category).where(
            Category.workspace_id.is_(None), Category.kind == "income"
        ).limit(1)
    )
    return {
        "user_id": user.id,
        "ws_id": ws_id,
        "acc_id": acc.id,
        "income_cat_id": income_cat.id,
    }


async def _make_income_tx(
    session: AsyncSession, *, ws_id: int, acc_id: int, cat_id: int, amount: int,
) -> Transaction:
    tx = Transaction(
        workspace_id=ws_id,
        kind="income",
        amount_minor=amount,
        to_account_id=acc_id,
        category_id=cat_id,
    )
    session.add(tx)
    await session.flush()  # tx.id для FK source_transaction_id
    return tx


async def test_single_envelope_skims_10_percent(db_session: AsyncSession, setup):
    env = Envelope(
        workspace_id=setup["ws_id"], name="НЗ", percent=10
    )
    db_session.add(env)
    await db_session.flush()

    tx = await _make_income_tx(
        db_session, ws_id=setup["ws_id"], acc_id=setup["acc_id"],
        cat_id=setup["income_cat_id"], amount=100000,  # 1000₽
    )
    entries = await skim_on_income(
        db_session, tx, actor_user_id=setup["user_id"]
    )
    await db_session.commit()

    assert len(entries) == 1
    assert entries[0].amount_minor == 10000  # floor(100000 * 10 / 100)
    assert entries[0].kind == "skim"
    assert entries[0].source_transaction_id == tx.id
    assert entries[0].created_by_user_id == setup["user_id"]


async def test_multiple_envelopes_each_skims_independently(db_session, setup):
    db_session.add_all([
        Envelope(workspace_id=setup["ws_id"], name="НЗ", percent=10),
        Envelope(workspace_id=setup["ws_id"], name="Отпуск", percent=15),
        Envelope(workspace_id=setup["ws_id"], name="Ручной", percent=None),
    ])
    await db_session.flush()

    tx = await _make_income_tx(
        db_session, ws_id=setup["ws_id"], acc_id=setup["acc_id"],
        cat_id=setup["income_cat_id"], amount=100000,
    )
    entries = await skim_on_income(
        db_session, tx, actor_user_id=setup["user_id"]
    )
    await db_session.commit()

    # Только два конверта с percent скимятся; ручной (percent=NULL) — нет.
    assert len(entries) == 2
    amounts = sorted(e.amount_minor for e in entries)
    assert amounts == [10000, 15000]


async def test_archived_envelope_does_not_skim(db_session, setup):
    from datetime import datetime, timezone
    db_session.add_all([
        Envelope(
            workspace_id=setup["ws_id"], name="Архив", percent=20,
            archived_at=datetime.now(timezone.utc),
        ),
        Envelope(workspace_id=setup["ws_id"], name="Активный", percent=10),
    ])
    await db_session.flush()

    tx = await _make_income_tx(
        db_session, ws_id=setup["ws_id"], acc_id=setup["acc_id"],
        cat_id=setup["income_cat_id"], amount=100000,
    )
    entries = await skim_on_income(
        db_session, tx, actor_user_id=setup["user_id"]
    )
    assert len(entries) == 1
    assert entries[0].amount_minor == 10000


async def test_manual_envelope_without_percent_does_not_skim(db_session, setup):
    """percent IS NULL — ручной конверт, не скимится."""
    db_session.add(
        Envelope(workspace_id=setup["ws_id"], name="Manual", percent=None)
    )
    await db_session.flush()

    tx = await _make_income_tx(
        db_session, ws_id=setup["ws_id"], acc_id=setup["acc_id"],
        cat_id=setup["income_cat_id"], amount=100000,
    )
    entries = await skim_on_income(
        db_session, tx, actor_user_id=setup["user_id"]
    )
    assert entries == []


async def test_small_amount_floor_zero_skipped(db_session, setup):
    """amount=50, pct=1 → floor(50/100)=0; entry не пишется (леджер чище)."""
    db_session.add(
        Envelope(workspace_id=setup["ws_id"], name="1%", percent=1)
    )
    await db_session.flush()

    tx = await _make_income_tx(
        db_session, ws_id=setup["ws_id"], acc_id=setup["acc_id"],
        cat_id=setup["income_cat_id"], amount=50,
    )
    entries = await skim_on_income(
        db_session, tx, actor_user_id=setup["user_id"]
    )
    assert entries == []


async def test_floor_truncates_correctly(db_session, setup):
    """amount=333, pct=10 → floor(333*10/100) = floor(33.3) = 33."""
    db_session.add(
        Envelope(workspace_id=setup["ws_id"], name="НЗ", percent=10)
    )
    await db_session.flush()

    tx = await _make_income_tx(
        db_session, ws_id=setup["ws_id"], acc_id=setup["acc_id"],
        cat_id=setup["income_cat_id"], amount=333,
    )
    entries = await skim_on_income(
        db_session, tx, actor_user_id=setup["user_id"]
    )
    assert len(entries) == 1
    assert entries[0].amount_minor == 33


async def test_raise_on_non_income_kind(db_session, setup):
    """MF7: raise ValueError вместо assert — устойчиво к PYTHONOPTIMIZE=1.
    adjustment с to_account_id растит баланс, но не доход."""
    tx = Transaction(
        workspace_id=setup["ws_id"],
        kind="adjustment",
        amount_minor=1000,
        to_account_id=setup["acc_id"],
        category_id=setup["income_cat_id"],
    )
    db_session.add(tx)
    await db_session.flush()

    with pytest.raises(ValueError, match="adjustment"):
        await skim_on_income(
            db_session, tx, actor_user_id=setup["user_id"]
        )


async def test_no_active_envelopes_returns_empty(db_session, setup):
    tx = await _make_income_tx(
        db_session, ws_id=setup["ws_id"], acc_id=setup["acc_id"],
        cat_id=setup["income_cat_id"], amount=100000,
    )
    entries = await skim_on_income(
        db_session, tx, actor_user_id=setup["user_id"]
    )
    assert entries == []


async def test_cross_workspace_envelopes_not_skimmed(
    db_session: AsyncSession, setup
):
    """Конверт юзера B не должен скимиться при income юзера A."""
    user_b = await ensure_user_provisioned(
        db_session, TelegramUser(id=91002, first_name="Bob")
    )
    await db_session.commit()
    db_session.add(
        Envelope(
            workspace_id=user_b.active_workspace_id, name="Bob's", percent=50,
        )
    )
    # Юзер A's конверт:
    db_session.add(
        Envelope(workspace_id=setup["ws_id"], name="A's", percent=10)
    )
    await db_session.flush()

    tx = await _make_income_tx(
        db_session, ws_id=setup["ws_id"], acc_id=setup["acc_id"],
        cat_id=setup["income_cat_id"], amount=100000,
    )
    entries = await skim_on_income(
        db_session, tx, actor_user_id=setup["user_id"]
    )
    assert len(entries) == 1
    assert entries[0].amount_minor == 10000  # только A's 10%, не Bob's 50%
