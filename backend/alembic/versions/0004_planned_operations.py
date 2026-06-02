"""planned_operations + categories.parent_id + tx.planned_operation_id

Revision ID: 0004_planned_operations
Revises: 0003_workspaces_rekey
Create Date: 2026-06-02 21:00:00.000000

Phase 5 schema: новая таблица `planned_operations`, расширение `categories`
для подкатегорий и `transactions` для идемпотентности confirm плана.

Без backfill — все новые колонки nullable либо имеют server_default. Безопасно
докатить на VPS через migrate-then-serve (ADR-0006) без downtime'a.

Шаги (upgrade):
  1. CREATE TABLE planned_operations с 9 CHECK + partial index по status.
  2. ALTER categories ADD COLUMN parent_id (FK→categories RESTRICT).
  3. ALTER transactions ADD COLUMN planned_operation_id (FK→planned SET NULL).
  4. ALTER transactions ADD COLUMN occurrence_date.
  5. CREATE UNIQUE INDEX transactions_planned_uq partial WHERE
     planned_operation_id IS NOT NULL.

Downgrade — обратный порядок, drop_index ДО drop_column (иначе Postgres
ругается «cannot drop column because index depends on it»). Подтверждённые
tx сохраняются (workspace_id, amount, account, category, occurred_at — на
месте), но теряют ссылку на источник: `planned_operation_id` и
`occurrence_date` дропнуты. Re-confirm тех же будущих вхождений после
повторного upgrade породит дубли — это ожидаемо, downgrade=ручной rollback.
"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_planned_operations"
down_revision: Union[str, Sequence[str], None] = "0003_workspaces_rekey"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. planned_operations ──────────────────────────────────────────────────
    op.create_table(
        "planned_operations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column(
            "currency", sa.CHAR(length=3), server_default="RUB", nullable=False
        ),
        # MF10-1: category_id NOT NULL — выровнено с tx XOR CHECK.
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("first_date", sa.Date(), nullable=False),
        sa.Column("recurrence", sa.Text(), nullable=False),
        sa.Column("total_cycles", sa.Integer(), nullable=True),
        sa.Column(
            "completed_cycles", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "status", sa.Text(), server_default="planned", nullable=False
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('income','expense')", name="planned_kind_chk"
        ),
        sa.CheckConstraint("amount_minor > 0", name="planned_amount_chk"),
        sa.CheckConstraint("currency = 'RUB'", name="planned_currency_chk"),
        sa.CheckConstraint(
            "recurrence IN ('once','week','month','year')",
            name="planned_recurrence_chk",
        ),
        sa.CheckConstraint(
            "status IN ('planned','paused','done')", name="planned_status_chk"
        ),
        sa.CheckConstraint("completed_cycles >= 0", name="planned_completed_chk"),
        sa.CheckConstraint(
            "total_cycles IS NULL OR total_cycles >= 1",
            name="planned_total_chk",
        ),
        sa.CheckConstraint(
            "total_cycles IS NULL OR completed_cycles <= total_cycles",
            name="planned_cycles_bounds_chk",
        ),
        sa.CheckConstraint(
            "(recurrence = 'once' AND total_cycles IS NULL) OR recurrence <> 'once'",
            name="planned_once_no_total_chk",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["category_id"], ["categories.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "planned_ws_status_idx",
        "planned_operations",
        ["workspace_id", "status"],
        unique=False,
        postgresql_where="archived_at IS NULL",
    )

    # ── 2. categories.parent_id ────────────────────────────────────────────────
    op.add_column(
        "categories",
        sa.Column("parent_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "categories_parent_id_fkey",
        "categories",
        "categories",
        ["parent_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # ── 3-4. transactions.planned_operation_id + occurrence_date ──────────────
    op.add_column(
        "transactions",
        sa.Column("planned_operation_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "transactions_planned_operation_id_fkey",
        "transactions",
        "planned_operations",
        ["planned_operation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "transactions",
        sa.Column("occurrence_date", sa.Date(), nullable=True),
    )

    # ── 5. partial unique transactions_planned_uq ─────────────────────────────
    op.create_index(
        "transactions_planned_uq",
        "transactions",
        ["planned_operation_id", "occurrence_date"],
        unique=True,
        postgresql_where="planned_operation_id IS NOT NULL",
    )


def downgrade() -> None:
    # Drop в обратном порядке. КРИТИЧНО: drop_index ДО drop_column,
    # иначе Postgres «cannot drop column because index depends on it».
    op.drop_index(
        "transactions_planned_uq",
        table_name="transactions",
        postgresql_where="planned_operation_id IS NOT NULL",
    )
    op.drop_column("transactions", "occurrence_date")
    op.drop_constraint(
        "transactions_planned_operation_id_fkey",
        "transactions",
        type_="foreignkey",
    )
    op.drop_column("transactions", "planned_operation_id")

    op.drop_constraint(
        "categories_parent_id_fkey", "categories", type_="foreignkey"
    )
    op.drop_column("categories", "parent_id")

    op.drop_index(
        "planned_ws_status_idx",
        table_name="planned_operations",
        postgresql_where="archived_at IS NULL",
    )
    op.drop_table("planned_operations")
