from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app import auth
from app.main import app

client = TestClient(app)
API = "/api/queue/worker-keys"
H = {"Authorization": "Bearer t"}


@pytest.fixture()
def signed_in(monkeypatch, make_profile):
    monkeypatch.setattr(auth.settings, "auth_enabled", True)
    monkeypatch.setattr(auth.settings, "supabase_publishable_key", "pk")
    monkeypatch.setattr(auth.settings, "auth_admin_emails", "admin@example.com")

    def as_user(status="approved"):
        uid = make_profile(status)
        monkeypatch.setattr(auth, "_fetch_supabase_user", AsyncMock(
            return_value={"id": uid, "email": "u@x", "user_metadata": {}}))
        return uid
    return as_user


def test_create_list_revoke(signed_in):
    signed_in()
    created = client.post(API, json={"name": "Asha's MacBook"}, headers=H)
    assert created.status_code == 201
    assert created.headers["cache-control"] == "no-store"
    body = created.json()
    assert set(body) == {"id", "name", "prefix", "created_at", "key"}
    assert body["key"].startswith("tqw_") and body["prefix"] == body["key"][:8]
    listed = client.get(API, headers=H).json()
    assert [k["name"] for k in listed] == ["Asha's MacBook"]
    assert set(listed[0]) == {"id", "name", "prefix", "created_at", "last_used_at", "revoked_at"}
    revoked = client.delete(f"{API}/{body['id']}", headers=H)
    assert revoked.status_code == 200 and revoked.json()["revoked_at"]
    assert "key" not in revoked.json() and "key_hash" not in revoked.json()


def test_legacy_prefix_works_too(signed_in):
    signed_in()
    assert client.get("/api/worker-keys", headers=H).status_code == 200


def test_other_users_key_is_404(signed_in):
    signed_in()
    key_id = client.post(API, json={"name": "a"}, headers=H).json()["id"]
    signed_in()
    assert client.delete(f"{API}/{key_id}", headers=H).status_code == 404
    assert client.get(API, headers=H).json() == []


def test_unknown_and_malformed_ids(signed_in):
    signed_in()
    assert client.delete(f"{API}/00000000-0000-0000-0000-000000000000",
                         headers=H).status_code == 404
    assert client.delete(f"{API}/not-a-uuid", headers=H).status_code == 422


def test_cap_is_409(signed_in):
    signed_in()
    for i in range(10):
        assert client.post(API, json={"name": f"k{i}"}, headers=H).status_code == 201
    resp = client.post(API, json={"name": "k10"}, headers=H)
    assert resp.status_code == 409 and "10" in resp.json()["detail"]


@pytest.mark.parametrize("status", ["pending", "rejected", "suspended"])
def test_only_approved_users(signed_in, status):
    signed_in(status)
    assert client.get(API, headers=H).status_code == 403
    assert client.post(API, json={"name": "k"}, headers=H).status_code == 403


def test_user_without_a_profile_is_403(signed_in, monkeypatch):
    signed_in()
    monkeypatch.setattr(auth, "_fetch_supabase_user", AsyncMock(return_value={
        "id": "00000000-0000-0000-0000-000000000001", "email": "u@x", "user_metadata": {}}))
    assert client.get(API, headers=H).status_code == 403


def test_needs_a_session(signed_in):
    assert client.get(API).status_code == 401


def test_name_is_required(signed_in):
    signed_in()
    assert client.post(API, json={"name": ""}, headers=H).status_code == 422
    assert client.post(API, json={"name": "x" * 101}, headers=H).status_code == 422


def test_needs_sign_in_enabled():
    assert client.get(API).status_code == 409


def test_worker_and_producer_keys_cannot_manage_keys(signed_in):
    signed_in()
    key = client.post(API, json={"name": "a"}, headers=H).json()["key"]
    assert client.get(API, headers={"X-API-Key": key}).status_code == 401


@pytest.mark.parametrize("upstream,expected", [(400, 401), (401, 401), (403, 401),
                                               (429, 503), (500, 503), (502, 503)])
def test_identity_service_errors_map_to_401_only_for_rejected_tokens(
        monkeypatch, upstream, expected):
    monkeypatch.setattr(auth.settings, "auth_enabled", True)
    monkeypatch.setattr(auth.settings, "supabase_publishable_key", "pk")
    monkeypatch.setattr(auth.settings, "auth_admin_emails", "admin@example.com")

    def handler(request):
        return httpx.Response(upstream, json={})
    real_client = httpx.AsyncClient
    monkeypatch.setattr(auth.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    assert client.get(API, headers=H).status_code == expected
