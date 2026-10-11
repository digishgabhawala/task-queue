"""HTTP-level tests for worker registration, heartbeat, listing and the admin
page. Uses the real local Postgres via the app's own get_db (see conftest)."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _register(session_id="session-1"):
    resp = client.post("/api/workers/register", json={
        "worker_id": "worker-1", "name": "Test Mac", "session_id": session_id,
        "capabilities": {"task_types": ["image_generation"]}, "version": "test",
    })
    assert resp.status_code == 200
    return resp.json()


def _heartbeat(**overrides):
    body = {"session_id": "session-1", "state": "idle"} | overrides
    return client.post("/api/workers/worker-1/heartbeat", json=body)


def test_registered_worker_is_listed_online_with_empty_details():
    _register()
    workers = client.get("/api/workers").json()
    assert [w["id"] for w in workers] == ["worker-1"]
    assert workers[0]["online"] is True
    assert workers[0]["details"] == {}


def test_heartbeat_details_are_returned_by_listing():
    _register()
    assert _heartbeat(details={"comfyui": {"state": "ready"}}).status_code == 200
    assert client.get("/api/workers").json()[0]["details"] == {"comfyui": {"state": "ready"}}


def test_heartbeat_from_replaced_session_is_rejected():
    _register()
    _register(session_id="session-2")
    resp = _heartbeat()
    assert resp.status_code == 409
    assert "newer process" in resp.json()["detail"]


def test_heartbeat_for_unknown_worker_is_rejected():
    resp = _heartbeat()
    assert resp.status_code == 409


def test_oversized_details_are_rejected():
    _register()
    resp = _heartbeat(details={"blob": "x" * 5000})
    assert resp.status_code == 422


def test_admin_page_renders():
    resp = client.get("/admin")
    assert resp.status_code == 200
    assert "Task Queue" in resp.text
    assert "AI Bharatiyaa" in resp.text
    assert 'aria-live="polite"' in resp.text
    assert "/admin-assets/tokens.css" in resp.text
    assert "/admin-assets/admin.css" in resp.text
    assert "/admin-assets/admin.js" in resp.text


def test_admin_assets_are_served():
    assert client.get("/admin-assets/admin.css").status_code == 200
    assert client.get("/admin-assets/admin.js").status_code == 200
    assert client.get("/admin-assets/tokens.css").status_code == 200
