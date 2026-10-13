from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_tables():
    """Auth guard tests complete before route handlers access the database."""
    yield


def _enable_auth(monkeypatch):
    monkeypatch.setattr(auth.settings, "auth_enabled", True)
    monkeypatch.setattr(auth.settings, "supabase_publishable_key", "sb_publishable_test")
    monkeypatch.setattr(auth.settings, "auth_admin_emails", "admin@example.com")


def test_admin_identity_requires_bearer_token(monkeypatch):
    _enable_auth(monkeypatch)
    response = client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"

    workers_response = client.get("/api/workers")
    assert workers_response.status_code == 401


def test_allowed_admin_identity_is_returned(monkeypatch):
    _enable_auth(monkeypatch)
    fetch_user = AsyncMock(return_value={
        "id": "user-123",
        "email": "Admin@Example.com",
        "user_metadata": {"name": "Admin User"},
    })
    monkeypatch.setattr(auth, "_fetch_supabase_user", fetch_user)

    response = client.get(
        "/api/auth/me",
        headers={"Authorization": "Bearer valid-token"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": "user-123",
        "email": "Admin@Example.com",
        "name": "Admin User",
        "avatar_url": None,
    }
    fetch_user.assert_awaited_once_with("valid-token")


def test_authenticated_non_admin_is_forbidden(monkeypatch):
    _enable_auth(monkeypatch)
    monkeypatch.setattr(
        auth,
        "_fetch_supabase_user",
        AsyncMock(return_value={"id": "user-456", "email": "other@example.com"}),
    )

    response = client.get(
        "/api/auth/me",
        headers={"Authorization": "Bearer valid-token"},
    )
    assert response.status_code == 403


def test_public_auth_config_never_exposes_service_key(monkeypatch):
    _enable_auth(monkeypatch)
    monkeypatch.setattr(auth.settings, "supabase_url", "https://example.supabase.co")
    response = client.get("/api/auth/config")
    assert response.status_code == 200
    assert response.json() == {
        "enabled": True,
        "supabase_url": "https://example.supabase.co",
        "supabase_publishable_key": "sb_publishable_test",
    }
    assert "service" not in response.text.casefold()


def test_suite_scoped_auth_config_matches_legacy_route(monkeypatch):
    _enable_auth(monkeypatch)
    monkeypatch.setattr(auth.settings, "supabase_url", "https://example.supabase.co")

    legacy = client.get("/api/auth/config")
    scoped = client.get("/api/queue/auth/config")

    assert scoped.status_code == 200
    assert scoped.json() == legacy.json()
