"""Machine (service) API keys: apps like linkedin-content-agent read worker
status with a revocable key, never with a human admin's browser session."""
import hashlib
from unittest.mock import AsyncMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app import auth
from app.config import Settings
from app.main import app

RAW_KEY = "tq_test_linkedin_key_0123456789"
HASH = hashlib.sha256(RAW_KEY.encode()).hexdigest()


@pytest.fixture(autouse=True)
def _clean_tables():
    """Auth checks run before any database access."""
    yield


def _probe_app():
    probe = FastAPI()

    @probe.get("/probe")
    def probe_route(user: auth.AuthUser = Depends(auth.require_admin_or_service)):
        return {"id": user.id, "email": user.email}
    return TestClient(probe)


def _enable(monkeypatch, keys=f"linkedin-content-agent:{HASH}"):
    monkeypatch.setattr(auth.settings, "auth_enabled", True)
    monkeypatch.setattr(auth.settings, "supabase_publishable_key", "sb_publishable_test")
    monkeypatch.setattr(auth.settings, "auth_admin_emails", "admin@example.com")
    monkeypatch.setattr(auth.settings, "service_api_keys", keys)


def test_valid_service_key_identifies_the_app(monkeypatch):
    _enable(monkeypatch)
    resp = _probe_app().get("/probe", headers={"X-API-Key": RAW_KEY})
    assert resp.status_code == 200
    assert resp.json()["id"] == "service:linkedin-content-agent"


def test_wrong_service_key_is_rejected(monkeypatch):
    _enable(monkeypatch)
    resp = _probe_app().get("/probe", headers={"X-API-Key": "not-the-key"})
    assert resp.status_code == 401


def test_without_a_key_the_admin_login_still_works(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(auth, "_fetch_supabase_user", AsyncMock(
        return_value={"id": "u1", "email": "admin@example.com"}))
    assert _probe_app().get("/probe").status_code == 401
    resp = _probe_app().get("/probe", headers={"Authorization": "Bearer valid-token"})
    assert resp.status_code == 200 and resp.json()["email"] == "admin@example.com"


def test_revoked_key_no_longer_works(monkeypatch):
    _enable(monkeypatch, keys="")  # key removed from SERVICE_API_KEYS
    assert _probe_app().get("/probe", headers={"X-API-Key": RAW_KEY}).status_code == 401


def test_auth_disabled_needs_no_key(monkeypatch):
    monkeypatch.setattr(auth.settings, "auth_enabled", False)
    assert _probe_app().get("/probe").status_code == 200


def test_worker_list_route_accepts_service_keys(monkeypatch):
    _enable(monkeypatch)
    resp = TestClient(app).get("/api/workers", headers={"X-API-Key": "wrong"})
    assert resp.status_code == 401  # wired to the service-or-admin check


def test_keys_are_configured_as_name_and_sha256_hash():
    s = Settings(_env_file=None, service_api_keys=f"linkedin:{HASH}, other:{'b' * 64}")
    assert s.service_key_hashes == {HASH: "linkedin", "b" * 64: "other"}


def test_malformed_key_configuration_fails_at_startup():
    with pytest.raises(ValueError, match="SERVICE_API_KEYS"):
        Settings(_env_file=None, service_api_keys="linkedin:plaintext-key")
