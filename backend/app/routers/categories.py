from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.db.session import get_session
from app.models import Category, User
from app.schemas.category import (
    CategoryCreate,
    CategoryKind,
    CategoryOut,
    CategoryUpdate,
)

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("", response_model=list[CategoryOut])
async def list_categories(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    kind: CategoryKind | None = Query(default=None),
) -> list[Category]:
    """Системные (user_id IS NULL) + юзеровские. Фильтр kind опционален."""
    stmt = select(Category).where(
        or_(Category.user_id.is_(None), Category.user_id == user.id)
    )
    if kind is not None:
        stmt = stmt.where(Category.kind == kind)
    stmt = stmt.order_by(Category.user_id.nulls_first(), Category.id)
    return list((await session.execute(stmt)).scalars().all())


@router.post("", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
async def create_category(
    body: CategoryCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Category:
    cat = Category(user_id=user.id, **body.model_dump())
    session.add(cat)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        if "categories_user_name_uq" in str(e.orig):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "category with this name already exists",
            ) from e
        raise
    await session.refresh(cat)
    return cat


@router.patch("/{category_id}", response_model=CategoryOut)
async def update_category(
    category_id: int,
    body: CategoryUpdate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Category:
    # Сначала смотрим есть ли категория вообще (через системные тоже).
    cat = await session.scalar(
        select(Category).where(Category.id == category_id)
    )
    if cat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "category not found")

    # Системные (user_id IS NULL) — read-only для всех. 403, не 404:
    # их существование публично (все юзеры их видят в GET).
    if cat.user_id is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "system category cannot be modified; create your own copy",
        )

    # Чужая пользовательская категория → 404 (не палим существование).
    if cat.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "category not found")

    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(cat, field, value)

    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        if "categories_user_name_uq" in str(e.orig):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "category with this name already exists",
            ) from e
        raise
    await session.refresh(cat)
    return cat
