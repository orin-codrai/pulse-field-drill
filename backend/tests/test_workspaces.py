"""Tests for /api/workspaces router (Phase 4 foundation).

Покрываем:
- GET /api/workspaces — юзер видит свои workspaces (personal на старте).
- PATCH /api/workspaces/active — happy: переключение на свой workspace.
- PATCH /api/workspaces/active — must-fix #8: чужой workspace → 403 ДО записи
  (active_workspace_id не должен поменяться).
- PATCH /api/workspaces/active — несуществующий workspace_id → 403, не 500.
- 401 без auth header'а.
- Ре-валидация membership в current_workspace: если юзера выкинули из
  workspace (UPDATE мимо API), следующий запрос ловит 403.
"""

from httpx import AsyncClient

from tests.conftest import sign_init_data


async def test_get_workspaces_requires_auth(app_client: AsyncClient):
    r = await app_client.get("/api/workspaces")
    assert r.status_code == 401


async def test_get_workspaces_returns_personal(
    app_client: AsyncClient, provisioned_user, auth_header
):
    r = await app_client.get("/api/workspaces", headers=auth_header)
    assert r.status_code == 200, r.text
    items = r.json()
    assert len(items) == 1
    ws = items[0]
    assert ws["id"] == provisioned_user.active_workspace_id
    assert ws["kind"] == "personal"
    assert ws["name"] == "Личный"


async def test_switch_to_own_workspace_happy(
    app_client: AsyncClient, provisioned_user, auth_header
):
    r = await app_client.patch(
        "/api/workspaces/active",
        headers=auth_header,
        json={"workspace_id": provisioned_user.active_workspace_id},
    )
    assert r.status_code == 200, r.text
    assert r.json()["id"] == provisioned_user.active_workspace_id


async def test_switch_to_non_member_workspace_403(
    app_client: AsyncClient, provisioned_user, auth_header, db_session
):
    """MUST-FIX #8 (ADR-0009 §4): юзер не может выставить active_workspace_id
    на чужой workspace. Иначе все последующие `WHERE workspace_id = ...`
    прошли бы и любой ресурс другого юзера утёк.

    Проверяем что: (а) 403, (б) active_workspace_id в БД НЕ изменился.
    """
    # Создаём user_b с его собственным personal workspace.
    from app.schemas.user import TelegramUser
    from app.services.user_provisioning import ensure_user_provisioned

    user_b = await ensure_user_provisioned(
        db_session, TelegramUser(id=55555, first_name="Bob")
    )
    await db_session.commit()

    # User A (auth_header, tg_id=12345) пытается переключиться на ws B.
    original_ws = provisioned_user.active_workspace_id
    r = await app_client.patch(
        "/api/workspaces/active",
        headers=auth_header,
        json={"workspace_id": user_b.active_workspace_id},
    )
    assert r.status_code == 403

    # Состояние не изменилось — проверяем через свежий SELECT.
    from sqlalchemy import select

    from app.models import User

    a = await db_session.scalar(select(User).where(User.id == provisioned_user.id))
    assert a.active_workspace_id == original_ws


async def test_switch_to_non_existing_workspace_403(
    app_client: AsyncClient, provisioned_user, auth_header
):
    """ID, которого нет в БД, обрабатывается так же как «не член» — 403.
    Не 404: не палим существование чужих workspace через дискриминацию кодов.
    """
    r = await app_client.patch(
        "/api/workspaces/active",
        headers=auth_header,
        json={"workspace_id": 999999},
    )
    assert r.status_code == 403


async def test_current_workspace_revalidates_membership_each_request(
    app_client: AsyncClient, provisioned_user, auth_header, db_session
):
    """ADR-0009 §4: current_workspace не доверяет сохранённому active_workspace_id
    вслепую. Симулируем «юзера выкинули из workspace» (DELETE через ORM),
    следующий запрос должен поймать 403, а не пройти по stale id.
    """
    from sqlalchemy import delete

    from app.models import WorkspaceMember

    # Сначала убеждаемся что под текущей фикстурой /api/accounts отвечает 200.
    r1 = await app_client.get("/api/accounts", headers=auth_header)
    assert r1.status_code == 200

    # Удаляем membership напрямую в БД (имитация: shared, второй юзер revoke'нул).
    await db_session.execute(
        delete(WorkspaceMember).where(
            WorkspaceMember.user_id == provisioned_user.id,
            WorkspaceMember.workspace_id == provisioned_user.active_workspace_id,
        )
    )
    await db_session.commit()

    r2 = await app_client.get("/api/accounts", headers=auth_header)
    assert r2.status_code == 403, r2.text


async def test_user_with_no_active_workspace_returns_401(
    app_client: AsyncClient, provisioned_user, db_session, valid_user
):
    """Юзер с active_workspace_id IS NULL (Phase 7 soft-delete сценарий)
    получает 401 на ресурсных endpoint'ах: контракт «сначала /api/me».
    """
    from sqlalchemy import update

    from app.models import User

    await db_session.execute(
        update(User)
        .where(User.id == provisioned_user.id)
        .values(active_workspace_id=None)
    )
    await db_session.commit()

    init = sign_init_data(valid_user)
    r = await app_client.get(
        "/api/accounts", headers={"Authorization": f"tma {init}"}
    )
    assert r.status_code == 401
