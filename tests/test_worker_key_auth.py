import hashlib
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app import auth
from app.db import SessionLocal, engine
from app.main import app
from app.services import worker_key_service as wks

ENV_KEY = "tq_env_worker_0123456789"
PRODUCER_KEY = "tq_env_producer_0123456789"
client = TestClient(app)


@pytest.fixture(autouse=True)
def _strict(monkeypatch, _platform_profiles):
    monkeypatch.setattr(auth.settings, "worker_api_keys",
                        f"env-worker:{hashlib.sha256(ENV_KEY.encode()).hexdigest()}")
    monkeypatch.setattr(auth.settings, "service_api_keys",
                        f"env-producer:{hashlib.sha256(PRODUCER_KEY.encode()).hexdigest()}")
    monkeypatch.setattr(auth.settings, "machine_auth_required", True)
    auth.clear_worker_key_cache()
    yield
    auth.clear_worker_key_cache()


def _register(key, worker_id="w-1"):
    return client.post("/api/queue/workers/register", headers={"X-API-Key": key}, json={
        "worker_id": worker_id, "name": "Laptop", "session_id": "s-1",
        "capabilities": {"task_types": ["probe"]}})


def _new_key(make_profile, status="approved"):
    uid = make_profile(status)
    with SessionLocal() as db:
        created = wks.create_key(db, user_id=uid, name="Laptop")
        return uid, created.raw_key, created.key.id


def _set_status(uid, status):
    with engine.begin() as conn:
        conn.execute(text("update platform.profiles set status = :s "
                          "where user_id = cast(:u as uuid)"), {"u": uid, "s": status})


def test_users_key_registers_a_worker_owned_by_that_user(make_profile):
    uid, key, _ = _new_key(make_profile)
    resp = _register(key)
    assert resp.status_code == 200 and resp.json()["owner_user_id"] == uid


def test_users_key_works_on_the_legacy_prefix_and_for_claims(make_profile):
    _, key, _ = _new_key(make_profile)
    resp = client.get("/api/tasks/next", headers={"X-API-Key": key},
                      params={"task_types": "nothing_waiting", "worker_id": "w-1"})
    assert resp.status_code == 204


def test_env_keys_still_work_and_own_nothing():
    resp = _register(ENV_KEY)
    assert resp.status_code == 200 and resp.json()["owner_user_id"] is None


def test_env_keys_never_touch_the_database():
    with patch.object(wks, "find_active_key", side_effect=AssertionError("db lookup")):
        assert _register(ENV_KEY).status_code == 200


def test_pending_users_key_is_rejected(make_profile):
    _, key, _ = _new_key(make_profile, status="pending")
    assert _register(key).status_code == 401


def test_suspended_owner_key_stops_after_cache_expiry(make_profile, monkeypatch):
    clock = [1_000_000.0]
    monkeypatch.setattr(auth, "_now", lambda: clock[0])
    uid, key, _ = _new_key(make_profile)
    assert _register(key).status_code == 200
    _set_status(uid, "suspended")
    assert _register(key).status_code == 200          # cached for up to 60 s
    clock[0] += 61
    assert _register(key).status_code == 401


def test_revoked_key_stops_after_cache_expiry(make_profile, monkeypatch):
    clock = [1_000_000.0]
    monkeypatch.setattr(auth, "_now", lambda: clock[0])
    uid, key, key_id = _new_key(make_profile)
    assert _register(key).status_code == 200
    with SessionLocal() as db:
        wks.revoke_key(db, user_id=uid, key_id=key_id)
    clock[0] += 59
    assert _register(key).status_code == 200
    clock[0] += 2
    assert _register(key).status_code == 401


def test_a_rejected_key_is_not_cached_so_a_new_key_works_at_once(make_profile):
    uid = make_profile()
    with SessionLocal() as db:
        created = wks.create_key(db, user_id=uid, name="Laptop")
    _set_status(uid, "pending")
    assert _register(created.raw_key).status_code == 401
    _set_status(uid, "approved")
    assert _register(created.raw_key).status_code == 200


def test_unknown_key_is_401():
    assert _register("tqw_not-a-real-key").status_code == 401


def test_users_key_cannot_act_as_a_producer(make_profile):
    _, key, _ = _new_key(make_profile)
    resp = client.post("/api/queue/tasks", headers={"X-API-Key": key},
                       json={"task_type": "probe", "payload": {}})
    assert resp.status_code == 401
    assert client.get("/api/queue/workers", headers={"X-API-Key": key}).status_code == 401


def test_producer_key_cannot_act_as_a_worker():
    assert _register(PRODUCER_KEY).status_code == 401


def test_users_key_cannot_take_over_another_owners_worker_id(make_profile):
    assert _register(ENV_KEY, worker_id="admin-laptop").status_code == 200
    _, key, _ = _new_key(make_profile)
    assert _register(key, worker_id="admin-laptop").status_code == 409
    _, other_key, _ = _new_key(make_profile)
    assert _register(other_key, worker_id="mine").status_code == 200
    assert _register(key, worker_id="mine").status_code == 409


def test_users_key_may_reregister_its_own_worker(make_profile):
    _, key, _ = _new_key(make_profile)
    assert _register(key, worker_id="mine").status_code == 200
    assert _register(key, worker_id="mine").status_code == 200


def test_database_outage_during_key_check_is_503_not_401():
    with patch.object(wks, "find_active_key",
                      side_effect=OperationalError("select", {}, Exception("down"))):
        assert _register("tqw_some-user-key").status_code == 503
