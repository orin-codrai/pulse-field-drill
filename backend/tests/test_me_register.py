"""GET /api/me + POST /api/me/register: дозаполнение профиля."""

from httpx import AsyncClient


async def test_me_returns_registration_required_for_new_user(
    app_client: AsyncClient, auth_header
):
    """Новый юзер (без display_name/consent) → registration_required=True."""
    r = await app_client.get("/api/me", headers=auth_header)
    assert r.status_code == 200
    body = r.json()
    assert body["registration_required"] is True
    assert body["display_name"] is None
    assert body["consent_at"] is None
    # MeOut.id = tg_id (для backward compat).
    assert body["id"] == 12345  # valid_user.id из conftest
    # MeOut.internal_id = ORM User.id (отдельное поле, MF14-6).
    assert "internal_id" in body
    assert body["internal_id"] != body["id"]


async def test_post_register_happy(app_client: AsyncClient, auth_header, provisioned_user):
    r = await app_client.post(
        "/api/me/register", headers=auth_header,
        json={"display_name": "Я", "email": "test@example.com", "consent": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["display_name"] == "Я"
    assert body["email"] == "test@example.com"
    assert body["consent_at"] is not None
    assert body["registration_required"] is False


async def test_post_register_consent_false_422(
    app_client: AsyncClient, auth_header, provisioned_user
):
    """Literal[True] отбивает False автоматом (MF14-5)."""
    r = await app_client.post(
        "/api/me/register", headers=auth_header,
        json={"display_name": "X", "consent": False},
    )
    assert r.status_code == 422


async def test_post_register_invalid_email_422(
    app_client: AsyncClient, auth_header, provisioned_user
):
    r = await app_client.post(
        "/api/me/register", headers=auth_header,
        json={"display_name": "X", "email": "not-an-email", "consent": True},
    )
    assert r.status_code == 422


async def test_post_register_extra_field_rejected(
    app_client: AsyncClient, auth_header, provisioned_user
):
    """extra='forbid' — нельзя выставить consent_at/deleted_at через body."""
    r = await app_client.post(
        "/api/me/register", headers=auth_header,
        json={
            "display_name": "X", "consent": True,
            "consent_at": "2000-01-01T00:00:00Z",  # mass-assignment попытка
        },
    )
    assert r.status_code == 422


async def test_me_out_helper_covers_all_fields():
    """MF15-2 + MF16-1 canary: расширение MeOut без _build_me_out → assert
    fails. exclude_unset=True ловит drift, который без него Pydantic
    скрыл бы defaults'ами."""
    from datetime import datetime, timezone

    from app.models import User
    from app.routers.me import _build_me_out
    from app.schemas.user import MeOut, TelegramUser

    fake_user = User(
        id=42, tg_id=42, first_name="X",
        active_workspace_id=None, display_name=None,
        email=None, consent_at=None, deleted_at=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    fake_tg = TelegramUser(id=42, first_name="X")
    built = _build_me_out(fake_user, fake_tg)
    explicit = set(built.model_dump(exclude_unset=True).keys())
    expected = set(MeOut.model_fields.keys())
    assert explicit == expected, (
        f"_build_me_out missing fields: {expected - explicit}; "
        f"extra fields: {explicit - expected}"
    )
