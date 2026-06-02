"""envelopes (rename from goals) + envelope_entries ledger + tx FK→RESTRICT

Revision ID: 0005_envelopes_from_goals
Revises: 0004_planned_operations
Create Date: 2026-06-03 00:00:00.000000

Phase 6: goals → envelopes rename + percent column + nullable target +
drop linked_account_id + new envelope_entries (event-sourced ledger) +
rewrite transactions.planned_operation_id_fkey on RESTRICT.

Constraint naming: новые CHECK добавляем под временными `goals_*` именами,
батч-rename в конце приводит к `envelopes_*` (Postgres rename table НЕ
переименовывает PK/CHECK constraint автоматически). Postgres DDL
transactional → частичный crash на любом шаге = полный rollback.

Backfill: НЕТ. Существующая `goals` — пустая (юзер не пользуется), все
новые колонки nullable или с server_default. Безопасный no-downtime
deploy через Dockerfile CMD migrate-then-serve (ADR-0006).

Шаги (см. docs/phase-6-plan-draft.md §6.1):
  1. ALTER goals ADD COLUMN percent INTEGER + CHECK goals_percent_chk
  2. ALTER goals.target_amount_minor DROP NOT NULL
  3. DROP goals_target_chk + ADD goals_target_nullable_chk
  4. ALTER goals DROP COLUMN linked_account_id
  5. ALTER TABLE goals RENAME TO envelopes
  6. ALTER INDEX goals_ws_active_idx RENAME TO envelopes_ws_active_idx
  7. ALTER envelopes ADD UNIQUE (id, workspace_id) — target композитного FK
  8. CREATE TABLE envelope_entries (composite FK + sign CHECK + indices)
  9a. RENAME CONSTRAINT goals_* → envelopes_* (pkey/percent/target/currency/ws_fkey)
  9b. ALTER transactions: DROP+ADD planned_operation_id_fkey on RESTRICT (C13-1)

Downgrade: 9b → 9a → 8 → ... → 1. Guards: fail если есть envelopes с
target_amount_minor IS NULL (не вернётся в NOT NULL); fail если есть
envelope_entries (drop_table безопасен, но история теряется — RAISE).
"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_envelopes_from_goals"
down_revision: Union[str, Sequence[str], None] = "0004_planned_operations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. percent column + CHECK (temp name) ─────────────────────────────────
    op.add_column(
        "goals", sa.Column("percent", sa.Integer(), nullable=True)
    )
    op.create_check_constraint(
        "goals_percent_chk",
        "goals",
        "percent IS NULL OR (percent > 0 AND percent <= 100)",
    )

    # ── 2. target_amount_minor NOT NULL → NULL ────────────────────────────────
    op.alter_column("goals", "target_amount_minor", nullable=True)

    # ── 3. Replace target CHECK with nullable variant ─────────────────────────
    op.drop_constraint("goals_target_chk", "goals", type_="check")
    op.create_check_constraint(
        "goals_target_nullable_chk",
        "goals",
        "target_amount_minor IS NULL OR target_amount_minor > 0",
    )

    # ── 4. Drop linked_account_id (FK сносится автоматически) ─────────────────
    op.drop_column("goals", "linked_account_id")

    # ── 5. RENAME TABLE ───────────────────────────────────────────────────────
    op.rename_table("goals", "envelopes")

    # ── 6. RENAME INDEX (не drop+create — pass-6 C3) ──────────────────────────
    op.execute(
        "ALTER INDEX goals_ws_active_idx RENAME TO envelopes_ws_active_idx"
    )

    # ── 7. UNIQUE(id, workspace_id) — target композитного FK (B1) ─────────────
    op.create_unique_constraint(
        "envelopes_id_ws_uq", "envelopes", ["id", "workspace_id"]
    )

    # ── 8. envelope_entries (composite FK + sign CHECK + indices) ─────────────
    op.create_table(
        "envelope_entries",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("envelope_id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("source_transaction_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('skim','manual','withdraw')",
            name="envelope_entries_kind_chk",
        ),
        sa.CheckConstraint(
            "(kind = 'withdraw' AND amount_minor < 0) "
            "OR (kind IN ('skim','manual') AND amount_minor > 0)",
            name="envelope_entries_sign_chk",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"
        ),
        # Композитный FK против дрейфа денормализации workspace_id (B1):
        sa.ForeignKeyConstraint(
            ["envelope_id", "workspace_id"],
            ["envelopes.id", "envelopes.workspace_id"],
            name="envelope_entries_envelope_fkey",
            ondelete="RESTRICT",
        ),
        # source_transaction_id CASCADE — DELETE income tx → entries уходят
        # (PIN-A pass 11; иначе reserved > balance после tx-DELETE):
        sa.ForeignKeyConstraint(
            ["source_transaction_id"], ["transactions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "envelope_entries_env_idx",
        "envelope_entries",
        ["envelope_id", "workspace_id"],
        unique=False,
    )
    op.create_index(
        "envelope_entries_source_tx_idx",
        "envelope_entries",
        ["source_transaction_id"],
        unique=False,
        postgresql_where="source_transaction_id IS NOT NULL",
    )

    # ── 9a. BATCH RENAME CONSTRAINT goals_* → envelopes_* (C6, C5) ────────────
    # ALTER TABLE ... RENAME TO ... в Postgres НЕ переименовывает PK/CHECK
    # constraint имена автоматически. Применяем явно.
    for old, new in [
        ("goals_pkey", "envelopes_pkey"),
        ("goals_percent_chk", "envelopes_percent_chk"),
        ("goals_target_nullable_chk", "envelopes_target_chk"),
        ("goals_currency_chk", "envelopes_currency_chk"),
        ("goals_workspace_id_fkey", "envelopes_workspace_id_fkey"),
    ]:
        op.execute(
            f'ALTER TABLE envelopes RENAME CONSTRAINT "{old}" TO "{new}"'
        )

    # ── 9b. Rewrite transactions.planned_operation_id_fkey on RESTRICT (MF12-1)─
    # P5-миграция 0004 создала FK с ondelete='SET NULL'. Это допускало обход
    # MF11-2 guard'a через DELETE plan → SET NULL → DELETE tx. После шага 9b
    # DELETE plan с confirmed tx → IntegrityError → 409 (handler в
    # delete_planned). Симметрично MF11-2 (DELETE tx из плана → 409).
    op.drop_constraint(
        "transactions_planned_operation_id_fkey",
        "transactions",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "transactions_planned_operation_id_fkey",
        "transactions",
        "planned_operations",
        ["planned_operation_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    """9b → 9a → 8 → ... → 1 (C13-2).

    Guards: fail если есть envelopes с target_amount_minor IS NULL (не
    вернётся в NOT NULL); fail если есть envelope_entries.
    """
    op.execute(
        "DO $$ BEGIN "
        "  IF EXISTS (SELECT 1 FROM envelope_entries) THEN "
        "    RAISE EXCEPTION 'downgrade невозможен: есть envelope_entries (история)'; "
        "  END IF; "
        "  IF EXISTS (SELECT 1 FROM envelopes WHERE target_amount_minor IS NULL) THEN "
        "    RAISE EXCEPTION 'downgrade невозможен: есть envelopes с target_amount_minor IS NULL'; "
        "  END IF; "
        "END $$;"
    )

    # 9b. tx FK обратно на SET NULL.
    op.drop_constraint(
        "transactions_planned_operation_id_fkey",
        "transactions",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "transactions_planned_operation_id_fkey",
        "transactions",
        "planned_operations",
        ["planned_operation_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 9a. RENAME CONSTRAINT envelopes_* → goals_*.
    for old, new in [
        ("envelopes_workspace_id_fkey", "goals_workspace_id_fkey"),
        ("envelopes_currency_chk", "goals_currency_chk"),
        ("envelopes_target_chk", "goals_target_nullable_chk"),
        ("envelopes_percent_chk", "goals_percent_chk"),
        ("envelopes_pkey", "goals_pkey"),
    ]:
        op.execute(
            f'ALTER TABLE envelopes RENAME CONSTRAINT "{old}" TO "{new}"'
        )

    # 8. envelope_entries down.
    op.drop_index(
        "envelope_entries_source_tx_idx",
        table_name="envelope_entries",
        postgresql_where="source_transaction_id IS NOT NULL",
    )
    op.drop_index("envelope_entries_env_idx", table_name="envelope_entries")
    op.drop_table("envelope_entries")

    # 7. unique(id, ws_id).
    op.drop_constraint(
        "envelopes_id_ws_uq", "envelopes", type_="unique"
    )

    # 6. RENAME INDEX обратно.
    op.execute(
        "ALTER INDEX envelopes_ws_active_idx RENAME TO goals_ws_active_idx"
    )

    # 5. RENAME TABLE.
    op.rename_table("envelopes", "goals")

    # 4. linked_account_id обратно.
    op.add_column(
        "goals",
        sa.Column("linked_account_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "goals_linked_account_id_fkey",
        "goals",
        "accounts",
        ["linked_account_id"],
        ["id"],
    )

    # 3. CHECK обратно (NOT NULL вариант).
    op.drop_constraint("goals_target_nullable_chk", "goals", type_="check")
    op.create_check_constraint(
        "goals_target_chk", "goals", "target_amount_minor > 0"
    )

    # 2. NOT NULL.
    op.alter_column("goals", "target_amount_minor", nullable=False)

    # 1. percent gone.
    op.drop_constraint("goals_percent_chk", "goals", type_="check")
    op.drop_column("goals", "percent")
