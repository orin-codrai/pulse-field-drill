"""Stubs для goals/budgets/reports: GET → 200 (empty), mutations → 501.

Цель — застолбить URL'ы и базовую форму ответа для фронта; полные
тела + Pydantic схемы + тесты — в Sprint 3b.
"""

from httpx import AsyncClient


async def test_reports_month_empty_shape(
    app_client: AsyncClient, provisioned_user, auth_header
):
    r = await app_client.get("/api/reports/month", headers=auth_header)
    assert r.status_code == 200
    body = r.json()
    # Empty shape — but the keys are stable, фронт может писать против контракта.
    assert set(body.keys()) == {"by_category", "by_kind", "total_expense", "total_income"}


async def test_reports_calendar_empty(
    app_client: AsyncClient, provisioned_user, auth_header
):
    r = await app_client.get("/api/reports/calendar", headers=auth_header)
    assert r.status_code == 200
    assert r.json() == []


async def test_stubs_require_auth(app_client: AsyncClient):
    for path in ["/api/reports/month", "/api/reports/calendar"]:
        r = await app_client.get(path)
        assert r.status_code == 401, f"{path} should 401 without auth"
