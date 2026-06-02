from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_workspace
from app.db.session import get_session
from app.models import Category, Goal, Transaction, Workspace
from app.services.resolvers import resolve_account
from app.schemas.goal import GoalCreate, GoalOut, GoalProgress, GoalUpdate
from app.services.balances import account_balance

router = APIRouter(prefix="/goals", tags=["goals"])


async def _validate_linked_account(
    session: AsyncSession, account_id: int, workspace_id: int, field: str
) -> None:
    """linked_account_id должен принадлежать workspace и быть активным.
    Mirror _validate_account_ref из transactions.py. Сигнатура с `field`
    выровнена с template'ом (C10-1 plan-reviewer pass 10).
    """
    acc = await resolve_account(session, account_id, workspace_id)
    if acc is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"{field}: not found"
        )
    if acc.archived_at is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"{field}: account is archived",
        )


@router.get("", response_model=list[GoalOut])
async def list_goals(
    ws: Workspace = Depends(current_workspace),
    session: AsyncSession = Depends(get_session),
) -> list[Goal]:
    stmt = select(Goal).where(Goal.workspace_id == ws.id).order_by(Goal.id)
    return list((await session.execute(stmt)).scalars().all())


@router.post("", response_model=GoalOut, status_code=status.HTTP_201_CREATED)
async def create_goal(
    body: GoalCreate,
    ws: Workspace = Depends(current_workspace),
    session: AsyncSession = Depends(get_session),
) -> Goal:
    if body.linked_account_id is not None:
        await _validate_linked_account(
            session, body.linked_account_id, ws.id, "linked_account_id"
        )
    goal = Goal(workspace_id=ws.id, **body.model_dump(exclude_none=True))
    session.add(goal)
    await session.commit()
    await session.refresh(goal)
    return goal


@router.patch("/{goal_id}", response_model=GoalOut)
async def update_goal(
    goal_id: int,
    body: GoalUpdate,
    ws: Workspace = Depends(current_workspace),
    session: AsyncSession = Depends(get_session),
) -> Goal:
    goal = await session.scalar(
        select(Goal).where(Goal.id == goal_id, Goal.workspace_id == ws.id)
    )
    if goal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "goal not found")

    updates = body.model_dump(exclude_unset=True)
    if "linked_account_id" in updates and updates["linked_account_id"] is not None:
        await _validate_linked_account(
            session, updates["linked_account_id"], ws.id, "linked_account_id"
        )

    for field, value in updates.items():
        setattr(goal, field, value)
    await session.commit()
    await session.refresh(goal)
    return goal


@router.delete("/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_goal(
    goal_id: int,
    ws: Workspace = Depends(current_workspace),
    session: AsyncSession = Depends(get_session),
) -> None:
    goal = await session.scalar(
        select(Goal).where(Goal.id == goal_id, Goal.workspace_id == ws.id)
    )
    if goal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "goal not found")
    await session.delete(goal)
    await session.commit()


@router.get("/{goal_id}/progress", response_model=GoalProgress)
async def goal_progress(
    goal_id: int,
    ws: Workspace = Depends(current_workspace),
    session: AsyncSession = Depends(get_session),
) -> GoalProgress:
    goal = await session.scalar(
        select(Goal).where(Goal.id == goal_id, Goal.workspace_id == ws.id)
    )
    if goal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "goal not found")

    if goal.linked_account_id is not None:
        current = await account_balance(session, goal.linked_account_id)
    else:
        # Unlinked goal: Σ income категории «Зарплата» (только системная) с
        # момента создания цели. См. must-fix #1 в plan v2.
        zarplata_id = await session.scalar(
            select(Category.id).where(
                Category.workspace_id.is_(None),
                Category.name == "Зарплата",
                Category.kind == "income",
            )
        )
        if zarplata_id is None:
            # Broken installation: системный seed повреждён. Не отдаём
            # тихий 0%, чтобы баг был виден.
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "system category 'Зарплата' missing (broken seed)",
            )
        total = await session.scalar(
            select(func.coalesce(func.sum(Transaction.amount_minor), 0)).where(
                Transaction.workspace_id == ws.id,
                Transaction.kind == "income",
                Transaction.category_id == zarplata_id,
                Transaction.occurred_at >= goal.created_at,
            )
        )
        current = int(total)

    target = goal.target_amount_minor
    percent = round(100.0 * current / target, 2) if target > 0 else 0.0

    today = datetime.now(timezone.utc).date()
    days_left: int | None = None
    if goal.target_date is not None:
        days_left = (goal.target_date - today).days

    # on_track: достиг цели — true; иначе линейная пропорция от прошедшего времени.
    # Без target_date — true только если current >= target.
    on_track: bool
    if current >= target:
        on_track = True
    elif goal.target_date is None:
        on_track = False
    else:
        created_date = goal.created_at.date()
        total_days = (goal.target_date - created_date).days
        elapsed_days = max(0, (today - created_date).days)
        if total_days <= 0:
            on_track = current >= target
        else:
            expected = target * min(1.0, elapsed_days / total_days)
            on_track = current >= expected

    return GoalProgress(
        current_minor=current,
        target_minor=target,
        percent=percent,
        days_left=days_left,
        on_track=on_track,
    )
