from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_workspace
from app.db.session import get_session
from app.models import Category, Workspace
from app.schemas.category import (
    CategoryCreate,
    CategoryKind,
    CategoryOut,
    CategoryUpdate,
)
from app.services.resolvers import resolve_category

router = APIRouter(prefix="/categories", tags=["categories"])


async def _validate_parent_ref(
    session: AsyncSession,
    parent_id: int,
    workspace_id: int,
    child_kind: str,
) -> None:
    """Проверки родителя при создании подкатегории (MF7/MF9-1).

    - Родитель существует и доступен (свой workspace или системный).
    - Родитель сам не подкатегория (глубина 2 — БД CHECK не видит другую
      строку, enforce здесь).
    - Родитель не archived.
    - kind-наследование: parent='both' пускает любого ребёнка; child='both'
      требует parent='both'; иначе совпадение kind.
    """
    parent = await resolve_category(session, parent_id, workspace_id)
    if parent is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "parent_id: not found"
        )
    if parent.parent_id is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "parent_id: nested deeper than 2 levels",
        )
    if parent.archived_at is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "parent_id: parent is archived",
        )
    if child_kind == "both" and parent.kind != "both":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "parent_id: child kind='both' requires parent kind='both'",
        )
    if parent.kind != "both" and parent.kind != child_kind:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"parent_id: kind mismatch (parent={parent.kind}, child={child_kind})",
        )


@router.get("", response_model=list[CategoryOut])
async def list_categories(
    ws: Workspace = Depends(current_workspace),
    session: AsyncSession = Depends(get_session),
    kind: CategoryKind | None = Query(default=None),
) -> list[Category]:
    """Системные (workspace_id IS NULL) + категории workspace. Фильтр kind опционален."""
    stmt = select(Category).where(
        or_(Category.workspace_id.is_(None), Category.workspace_id == ws.id)
    )
    if kind is not None:
        stmt = stmt.where(Category.kind == kind)
    stmt = stmt.order_by(Category.workspace_id.nulls_first(), Category.id)
    return list((await session.execute(stmt)).scalars().all())


@router.post("", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
async def create_category(
    body: CategoryCreate,
    ws: Workspace = Depends(current_workspace),
    session: AsyncSession = Depends(get_session),
) -> Category:
    # MF9-1: без этой проверки `parent_id` юзера принимается на веру; FK
    # на categories.id валидирует только существование, не membership →
    # парент чужого workspace проходит, дерево течёт между workspaces.
    if body.parent_id is not None:
        await _validate_parent_ref(session, body.parent_id, ws.id, body.kind)
    cat = Category(workspace_id=ws.id, **body.model_dump())
    session.add(cat)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        if "categories_ws_name_uq" in str(e.orig):
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
    ws: Workspace = Depends(current_workspace),
    session: AsyncSession = Depends(get_session),
) -> Category:
    # Сначала смотрим есть ли категория вообще (через системные тоже).
    cat = await session.scalar(
        select(Category).where(Category.id == category_id)
    )
    if cat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "category not found")

    # Системные (workspace_id IS NULL) — read-only для всех. 403, не 404:
    # их существование публично (все видят в GET).
    if cat.workspace_id is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "system category cannot be modified; create your own copy",
        )

    # Чужая (другой workspace) категория → 404 (не палим существование).
    if cat.workspace_id != ws.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "category not found")

    updates = body.model_dump(exclude_unset=True)

    # Архивация родителя с активными детьми — нельзя. Альтернатива (каскад
    # архивации) — backlog. Re-activate (archived_at=None) проверки не требует.
    archiving = (
        "archived_at" in updates
        and updates["archived_at"] is not None
        and cat.archived_at is None
    )
    if archiving:
        n_children = await session.scalar(
            select(func.count(Category.id)).where(
                Category.parent_id == cat.id, Category.archived_at.is_(None)
            )
        )
        if n_children:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "archive children first",
            )

    for field, value in updates.items():
        setattr(cat, field, value)

    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        if "categories_ws_name_uq" in str(e.orig):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "category with this name already exists",
            ) from e
        raise
    await session.refresh(cat)
    return cat
