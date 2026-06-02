"""sharing + audit: workspace_invites + audit_log + users registration/soft-delete

Revision ID: 0006_sharing_and_audit
Revises: 0005_envelopes_from_goals
Create Date: 2026-06-03 00:00:00.000000

Phase 7 schema: 4 nullable колонки на users (display_name/email/consent_at/
deleted_at) + workspace_invites + audit_log. Без backfill — всё nullable,
существующие 2 юзера переживают без вмешательства.

Postgres DDL transactional → частичный crash на любом шаге = полный rollback
(паттерн P5/P6 миграций).

Шаги (см. docs/phase-7-plan-draft.md §7.A):
  1. ALTER users ADD COLUMN display_name TEXT NULL
  2. ALTER users ADD COLUMN email TEXT NULL
  3. ALTER users ADD COLUMN consent_at TIMESTAMPTZ NULL
  4. ALTER users ADD COLUMN deleted_at TIMESTAMPTZ NULL
  5. CREATE TABLE workspace_invites + workspace_invites_ws_idx
  6. CREATE TABLE audit_log + 2 indices

Downgrade 6→1 с 4 guards (RAISE на audit/accepted invites/soft-deleted/
consent_at — compliance потеря). Auto-rollback от Postgres DDL transactional.
"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_sharing_and_audit"
down_revision: Union[str, Sequence[str], None] = "0005_envelopes_from_goals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1-4. users расширение ─────────────────────────────────────────────────
    op.add_column("users", sa.Column("display_name", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("email", sa.Text(), nullable=True))
    op.add_column(
        "users",
        sa.Column("consent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── 5. workspace_invites ──────────────────────────────────────────────────
    op.create_table(
        "workspace_invites",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("accepted_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "status", sa.Text(), server_default="pending", nullable=False
        ),
        sa.Column(
            "expires_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "accepted_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending','accepted','revoked','expired')",
            name="workspace_invites_status_chk",
        ),
        sa.CheckConstraint(
            "(status = 'accepted' AND accepted_at IS NOT NULL) "
            "OR (status <> 'accepted' AND accepted_at IS NULL)",
            name="workspace_invites_accepted_consistency_chk",
        ),
        sa.UniqueConstraint("token", name="workspace_invites_token_key"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "workspace_invites_ws_idx",
        "workspace_invites",
        ["workspace_id"],
        unique=False,
    )

    # ── 6. audit_log ──────────────────────────────────────────────────────────
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("snapshot_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "entity_type IN ('transaction','account')",
            name="audit_log_entity_type_chk",
        ),
        sa.CheckConstraint(
            "action IN ('create','update','delete')",
            name="audit_log_action_chk",
        ),
        sa.ForeignKeyConstraint(
            # RESTRICT: hard-purge удаляет audit явно ПЕРЕД workspace.
            ["workspace_id"], ["workspaces.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "audit_log_ws_created_idx",
        "audit_log",
        ["workspace_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "audit_log_entity_idx",
        "audit_log",
        ["workspace_id", "entity_type", "entity_id"],
        unique=False,
    )


def downgrade() -> None:
    """6→1 с 4 guards. Auto-rollback от Postgres DDL transactional."""
    op.execute(
        "DO $$ BEGIN "
        "  IF EXISTS (SELECT 1 FROM audit_log) THEN "
        "    RAISE EXCEPTION 'downgrade невозможен: есть audit_log записи (история)'; "
        "  END IF; "
        "  IF EXISTS (SELECT 1 FROM workspace_invites WHERE status = 'accepted') THEN "
        "    RAISE EXCEPTION 'downgrade невозможен: есть принятые invite (membership уже создан)'; "
        "  END IF; "
        "  IF EXISTS (SELECT 1 FROM users WHERE deleted_at IS NOT NULL) THEN "
        "    RAISE EXCEPTION 'downgrade невозможен: есть soft-deleted users'; "
        "  END IF; "
        "  IF EXISTS (SELECT 1 FROM users WHERE consent_at IS NOT NULL) THEN "
        "    RAISE EXCEPTION 'downgrade сотрёт consent_at — compliance потеря'; "
        "  END IF; "
        "END $$;"
    )
    op.drop_index("audit_log_entity_idx", table_name="audit_log")
    op.drop_index("audit_log_ws_created_idx", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index(
        "workspace_invites_ws_idx", table_name="workspace_invites"
    )
    op.drop_table("workspace_invites")
    op.drop_column("users", "deleted_at")
    op.drop_column("users", "consent_at")
    op.drop_column("users", "email")
    op.drop_column("users", "display_name")
