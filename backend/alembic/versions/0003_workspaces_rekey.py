"""workspaces + re-key owned tables (user_id → workspace_id)

Revision ID: 0003_workspaces_rekey
Revises: 7111ba4f0334
Create Date: 2026-06-02 12:00:00.000000

Phase 4 из docs/v1.0-plan.md. Migrate-then-serve (ADR-0006): downtime ОК,
конкурентного трафика нет — деплой в окно. Бэкап БД до накатки.

Шаги (порядок критичен, см. план §Phase 4 «Миграция»):
  1. Создать workspaces + workspace_members.
  2. ДО backfill — снять старые `<table>_user_id_fkey` CASCADE (CASCADE-ловушка
     pass-5 #1: пока FK CASCADE жив, удаление юзера снесло бы расшаренные данные).
  3. Backfill: каждому юзеру — personal workspace + owner-membership +
     users.active_workspace_id ← этот workspace.
  4. На 6 владеемых таблицах (accounts, transactions, categories, budgets,
     goals, receipts) добавить workspace_id; backfill через members; навесить
     FK → workspaces RESTRICT (без CASCADE — D4 «без каскада»).
  5. Системные категории остаются `workspace_id IS NULL` (через `user_id IS NULL`
     ничего не находится в членстве — workspace_id остаётся NULL естественно).
  6. Переписать partial unique / actively-named индексы (`*_user_*` → `*_ws_*`).
     COALESCE — только на categories (NULL=системная). На accounts/budgets ‒
     плоские, чтобы on_conflict_do_nothing(index_elements=[workspace_id, name])
     в provisioning сматчил индекс (pass-2 C4 + pass-5 C5).
  7. Drop старых user_id колонок (FK уже снят на шаге 2).
  8. created_by_user_id (nullable, SET NULL) на transactions и accounts —
     фундамент аудита, заполняться будет с Phase 7. Pre-sharing строки = NULL,
     это ожидаемо (план C4).

Downgrade: восстанавливает 0002-state (`user_id` обратно из members). Работает
только если все workspaces ещё personal (1 member = 1 user); shared (Phase 7+)
сделает downgrade невозможным — добавляем guard.
"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_workspaces_rekey"
down_revision: Union[str, Sequence[str], None] = "7111ba4f0334"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Имена старых FK constraint'ов, которые надо снять перед backfill. Alembic
# autogenerate использует convention `<table>_<col>_fkey` — это они и есть.
_OLD_USER_FKS: list[tuple[str, str]] = [
    ("accounts", "accounts_user_id_fkey"),
    ("categories", "categories_user_id_fkey"),
    ("transactions", "transactions_user_id_fkey"),
    ("budgets", "budgets_user_id_fkey"),
    ("goals", "goals_user_id_fkey"),
    ("receipts", "receipts_user_id_fkey"),
]


def upgrade() -> None:
    # ── 1. Workspaces + members ────────────────────────────────────────────────
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('personal','shared')", name="workspaces_kind_chk"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "workspace_members",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "role", sa.Text(), server_default="owner", nullable=False
        ),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('owner','member')", name="workspace_members_role_chk"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "workspace_id", "user_id", name="workspace_members_uq"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── 2. Снять старые CASCADE FK ДО backfill (CASCADE-ловушка) ───────────────
    for table, fk_name in _OLD_USER_FKS:
        op.drop_constraint(fk_name, table, type_="foreignkey")

    # ── 3. users.active_workspace_id (заполним в backfill) ─────────────────────
    op.add_column(
        "users",
        sa.Column("active_workspace_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "users_active_workspace_id_fkey",
        "users",
        "workspaces",
        ["active_workspace_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # ── 4. Backfill: один personal workspace на юзера + членство + active. ────
    # Делаем построчно через DO-блок: один INSERT в workspaces → знаем id →
    # INSERT в workspace_members + UPDATE users.active_workspace_id. INSERT
    # с RETURNING + JOIN на users неудобен (нет связи между новой строкой ws
    # и source user); процедурный путь короче.
    op.execute(
        """
        DO $$
        DECLARE
            u RECORD;
            ws_id INT;
        BEGIN
            FOR u IN SELECT id FROM users ORDER BY id LOOP
                INSERT INTO workspaces (name, kind)
                VALUES ('Личный', 'personal')
                RETURNING id INTO ws_id;

                INSERT INTO workspace_members (workspace_id, user_id, role)
                VALUES (ws_id, u.id, 'owner');

                UPDATE users SET active_workspace_id = ws_id WHERE id = u.id;
            END LOOP;
        END $$;
        """
    )

    # ── 5. На 6 владеемых таблицах: добавить workspace_id, backfill, FK. ──────
    # accounts / transactions / budgets / goals / receipts — workspace_id NOT NULL.
    # categories — workspace_id NULLABLE (NULL = системная).
    op.add_column(
        "accounts", sa.Column("workspace_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "transactions", sa.Column("workspace_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "categories", sa.Column("workspace_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "budgets", sa.Column("workspace_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "goals", sa.Column("workspace_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "receipts", sa.Column("workspace_id", sa.Integer(), nullable=True)
    )

    # Backfill: для каждой строки workspace_id ← workspace, в котором юзер owner.
    # На текущей схеме у каждого юзера ровно один personal workspace → join 1-к-1.
    # categories.user_id IS NULL → workspace_id остаётся NULL (системные).
    for table in ("accounts", "transactions", "budgets", "goals", "receipts"):
        op.execute(
            f"""
            UPDATE {table} t
            SET workspace_id = wm.workspace_id
            FROM workspace_members wm
            WHERE wm.user_id = t.user_id
            """
        )
    op.execute(
        """
        UPDATE categories c
        SET workspace_id = wm.workspace_id
        FROM workspace_members wm
        WHERE wm.user_id = c.user_id
          AND c.user_id IS NOT NULL
        """
    )

    # NOT NULL на всех кроме categories.
    for table in ("accounts", "transactions", "budgets", "goals", "receipts"):
        op.alter_column(table, "workspace_id", nullable=False)

    # FK → workspaces RESTRICT (без CASCADE, D4).
    for table in (
        "accounts",
        "transactions",
        "categories",
        "budgets",
        "goals",
        "receipts",
    ):
        op.create_foreign_key(
            f"{table}_workspace_id_fkey",
            table,
            "workspaces",
            ["workspace_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    # ── 6. Индексы: drop старые `*_user_*`, create новые `*_ws_*` ──────────────
    # accounts
    op.drop_index(
        "accounts_user_active_idx",
        table_name="accounts",
        postgresql_where="archived_at IS NULL",
    )
    op.drop_index(
        "accounts_user_name_uq",
        table_name="accounts",
        postgresql_where="archived_at IS NULL",
    )
    op.create_index(
        "accounts_ws_active_idx",
        "accounts",
        ["workspace_id"],
        unique=False,
        postgresql_where="archived_at IS NULL",
    )
    op.create_index(
        "accounts_ws_name_uq",
        "accounts",
        ["workspace_id", "name"],
        unique=True,
        postgresql_where="archived_at IS NULL",
    )

    # categories — expression-индекс с COALESCE (NULL=системная).
    op.execute("DROP INDEX IF EXISTS categories_user_name_uq")
    op.execute(
        "CREATE UNIQUE INDEX categories_ws_name_uq "
        "ON categories (COALESCE(workspace_id, 0), name) "
        "WHERE archived_at IS NULL"
    )

    # budgets — плоский (workspace_id NOT NULL).
    op.drop_index(
        "budgets_active_uq",
        table_name="budgets",
        postgresql_where="archived_at IS NULL",
    )
    op.create_index(
        "budgets_active_uq",
        "budgets",
        ["workspace_id", "category_id", "period"],
        unique=True,
        postgresql_where="archived_at IS NULL",
    )

    # goals
    op.drop_index(
        "goals_user_active_idx",
        table_name="goals",
        postgresql_where="archived_at IS NULL",
    )
    op.create_index(
        "goals_ws_active_idx",
        "goals",
        ["workspace_id"],
        unique=False,
        postgresql_where="archived_at IS NULL",
    )

    # receipts
    op.drop_index("receipts_user_created_idx", table_name="receipts")
    op.create_index(
        "receipts_ws_created_idx",
        "receipts",
        ["workspace_id", "created_at"],
        unique=False,
    )

    # transactions: DESC на occurred_at — через op.execute (как и в 0001).
    op.execute("DROP INDEX IF EXISTS transactions_user_occurred_idx")
    op.execute(
        "CREATE INDEX transactions_ws_occurred_idx "
        "ON transactions (workspace_id, occurred_at DESC)"
    )

    # ── 7. Drop старых user_id колонок ────────────────────────────────────────
    op.drop_column("accounts", "user_id")
    op.drop_column("transactions", "user_id")
    op.drop_column("categories", "user_id")
    op.drop_column("budgets", "user_id")
    op.drop_column("goals", "user_id")
    op.drop_column("receipts", "user_id")

    # ── 8. created_by_user_id — фундамент аудита (Phase 7) ────────────────────
    op.add_column(
        "accounts",
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "accounts_created_by_user_id_fkey",
        "accounts",
        "users",
        ["created_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "transactions",
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "transactions_created_by_user_id_fkey",
        "transactions",
        "users",
        ["created_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Reverse to 0002-state.

    Работает только если все workspaces ещё personal (1 member = 1 user) —
    т.е. до Phase 7 sharing. Если найдём shared workspace — fail-fast: иначе
    при reverse user_id для shared-строк будет неоднозначным.
    """
    op.execute(
        "DO $$ BEGIN "
        "  IF EXISTS (SELECT 1 FROM workspaces WHERE kind = 'shared') THEN "
        "    RAISE EXCEPTION 'downgrade невозможен: есть shared workspace'; "
        "  END IF; "
        "END $$;"
    )

    # Аудит-колонки — снимаем первыми.
    op.drop_constraint(
        "transactions_created_by_user_id_fkey",
        "transactions",
        type_="foreignkey",
    )
    op.drop_column("transactions", "created_by_user_id")
    op.drop_constraint(
        "accounts_created_by_user_id_fkey", "accounts", type_="foreignkey"
    )
    op.drop_column("accounts", "created_by_user_id")

    # Вернуть user_id колонки на 6 таблиц.
    for table in (
        "accounts",
        "transactions",
        "categories",
        "budgets",
        "goals",
        "receipts",
    ):
        op.add_column(
            table, sa.Column("user_id", sa.BigInteger(), nullable=True)
        )

    # Backfill из членства (1-to-1 потому что только personal).
    for table in ("accounts", "transactions", "budgets", "goals", "receipts"):
        op.execute(
            f"""
            UPDATE {table} t
            SET user_id = wm.user_id
            FROM workspace_members wm
            WHERE wm.workspace_id = t.workspace_id
            """
        )
    op.execute(
        """
        UPDATE categories c
        SET user_id = wm.user_id
        FROM workspace_members wm
        WHERE wm.workspace_id = c.workspace_id
          AND c.workspace_id IS NOT NULL
        """
    )

    # NOT NULL вернуть везде кроме categories (там 0001-state nullable).
    for table in ("accounts", "transactions", "budgets", "goals", "receipts"):
        op.alter_column(table, "user_id", nullable=False)

    # Новые FK снять, старые CASCADE FK вернуть.
    for table, fk_name in _OLD_USER_FKS:
        op.drop_constraint(
            f"{table}_workspace_id_fkey", table, type_="foreignkey"
        )
        op.create_foreign_key(
            fk_name, table, "users", ["user_id"], ["id"], ondelete="CASCADE"
        )

    # Индексы: snimam `*_ws_*`, vraщam `*_user_*`.
    op.drop_index(
        "accounts_ws_active_idx",
        table_name="accounts",
        postgresql_where="archived_at IS NULL",
    )
    op.drop_index(
        "accounts_ws_name_uq",
        table_name="accounts",
        postgresql_where="archived_at IS NULL",
    )
    op.create_index(
        "accounts_user_active_idx",
        "accounts",
        ["user_id"],
        unique=False,
        postgresql_where="archived_at IS NULL",
    )
    op.create_index(
        "accounts_user_name_uq",
        "accounts",
        ["user_id", "name"],
        unique=True,
        postgresql_where="archived_at IS NULL",
    )

    op.execute("DROP INDEX IF EXISTS categories_ws_name_uq")
    op.execute(
        "CREATE UNIQUE INDEX categories_user_name_uq "
        "ON categories (COALESCE(user_id, 0), name) "
        "WHERE archived_at IS NULL"
    )

    op.drop_index(
        "budgets_active_uq",
        table_name="budgets",
        postgresql_where="archived_at IS NULL",
    )
    op.create_index(
        "budgets_active_uq",
        "budgets",
        ["user_id", "category_id", "period"],
        unique=True,
        postgresql_where="archived_at IS NULL",
    )

    op.drop_index(
        "goals_ws_active_idx",
        table_name="goals",
        postgresql_where="archived_at IS NULL",
    )
    op.create_index(
        "goals_user_active_idx",
        "goals",
        ["user_id"],
        unique=False,
        postgresql_where="archived_at IS NULL",
    )

    op.drop_index("receipts_ws_created_idx", table_name="receipts")
    op.create_index(
        "receipts_user_created_idx",
        "receipts",
        ["user_id", "created_at"],
        unique=False,
    )

    op.execute("DROP INDEX IF EXISTS transactions_ws_occurred_idx")
    op.execute(
        "CREATE INDEX transactions_user_occurred_idx "
        "ON transactions (user_id, occurred_at DESC)"
    )

    # Дропнуть workspace_id колонки и users.active_workspace_id.
    for table in (
        "accounts",
        "transactions",
        "categories",
        "budgets",
        "goals",
        "receipts",
    ):
        op.drop_column(table, "workspace_id")

    op.drop_constraint(
        "users_active_workspace_id_fkey", "users", type_="foreignkey"
    )
    op.drop_column("users", "active_workspace_id")

    op.drop_table("workspace_members")
    op.drop_table("workspaces")
