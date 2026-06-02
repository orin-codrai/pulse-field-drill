"""Резолверы доменных сущностей с учётом workspace-scope.

Чистые SQLAlchemy-функции без HTTP-зависимостей: возвращают `Model | None`,
caller решает что делать с None (404 для GET, 422 для FK-валидации). Извлечены
из роутерских mirrors (`_validate_*_ref`), чтобы scope-правила жили в одном
месте — иначе при расширении (например, разрешить cross-workspace в шаринге)
пришлось бы править 3-4 копии.
"""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, Category


async def resolve_category(
    session: AsyncSession, category_id: int, workspace_id: int
) -> Category | None:
    """Системная (workspace_id IS NULL) ИЛИ принадлежащая workspace."""
    return await session.scalar(
        select(Category).where(
            Category.id == category_id,
            or_(Category.workspace_id.is_(None), Category.workspace_id == workspace_id),
        )
    )


async def resolve_account(
    session: AsyncSession, account_id: int, workspace_id: int
) -> Account | None:
    """Account, принадлежащий workspace."""
    return await session.scalar(
        select(Account).where(
            Account.id == account_id, Account.workspace_id == workspace_id
        )
    )
