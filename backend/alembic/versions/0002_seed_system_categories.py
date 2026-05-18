"""seed system categories

Revision ID: 7111ba4f0334
Revises: 23ef8b6743ad
Create Date: 2026-05-18 09:19:20.811708

"""
from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7111ba4f0334'
down_revision: Union[str, Sequence[str], None] = '23ef8b6743ad'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Инлайн копия app/seed/system_categories.py — миграция не должна зависеть
# от app/ кода (snapshot semantic: будущий рефакторинг не должен менять
# поведение старой миграции).
SYSTEM_CATEGORIES: list[tuple[str, str, str]] = [
    ("👶", "Дети", "expense"),
    ("🏠", "Дом. уют", "expense"),
    ("💆", "Забота о себе", "expense"),
    ("💊", "Здоровье", "expense"),
    ("🍽️", "Кафе и рестораны", "expense"),
    ("💡", "Коммуналка", "expense"),
    ("🚙", "Машина", "expense"),
    ("📚", "Образование", "expense"),
    ("💳", "Платежи, комиссии", "expense"),
    ("🎁", "Подарки", "expense"),
    ("🔔", "Подписки", "expense"),
    ("🛍️", "Покупки", "expense"),
    ("🛒", "Продукты", "expense"),
    ("✈️", "Путешествия", "expense"),
    ("🎬", "Развлечения", "expense"),
    ("🚇", "Транспорт", "expense"),
    ("💰", "Зарплата", "income"),
    ("⚖️", "Корректировка", "both"),
]


def upgrade() -> None:
    categories_table = sa.table(
        "categories",
        sa.column("user_id", sa.BigInteger()),
        sa.column("name", sa.Text()),
        sa.column("kind", sa.Text()),
        sa.column("icon", sa.Text()),
    )
    op.bulk_insert(
        categories_table,
        [
            {"user_id": None, "name": name, "kind": kind, "icon": icon}
            for icon, name, kind in SYSTEM_CATEGORIES
        ],
    )


def downgrade() -> None:
    # Системные = user_id IS NULL. Единственный признак (is_system выкинут).
    op.execute("DELETE FROM categories WHERE user_id IS NULL")
