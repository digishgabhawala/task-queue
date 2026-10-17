"""Every machine endpoint rejects missing and wrong-role credentials.

The application mounts the same router at both its legacy ``/api`` prefix
and its portfolio ``/api/queue`` prefix, so the matrix deliberately checks
both. Successful responses may be 404/409 when the target does not exist;
that proves authentication passed and the handler was reached.
"""
import hashlib
import uuid
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.main import app

PRODUCER_KEY = "tq_test_producer_0123456789"
WORKER_KEY = "tq_test_worker_0123456789"
PRODUCER_HASH = hashlib.sha256(PRODUCER_KEY.encode()).hexdigest()
WORKER_HASH = hashlib.sha256(WORKER_KEY.encode()).hexdigest()
# Unknown worker keys are looked up as users' keys (joined to platform.profiles).
pytestmark = pytest.mark.usefixtures("_platform_profiles")


@pytest.fixture(autouse=True)
def _enforce_machine_auth(monkeypatch):
    monkeypatch.setattr(auth.settings, "auth_enabled", True)
    monkeypatch.setattr(auth.settings, "supabase_publishable_key", "sb_publishable_test")
    monkeypatch.setattr(auth.settings, "auth_admin_emails", "admin@example.com")
    monkeypatch.setattr(auth.settings, "service_api_keys", f"test-producer:{PRODUCER_HASH}")
    monkeypatch.setattr(auth.settings, "worker_api_keys", f"test-worker:{WORKER_HASH}")
    monkeypatch.setattr(auth.settings, "machine_auth_required", True)


@pytest.fixture()
def client():
    return TestClient(app)


@dataclass(frozen=True)
class RouteCase:
    method: str
    path: str
    role: str
    expected: int
    json: dict | None = None
    params: dict | None = None
    files: dict | None = None


UNKNOWN_ID = str(uuid.uuid4())
CASES = (
    RouteCase("get", "/workers", "producer", 200),
    RouteCase("post", "/tasks", "producer", 200,
              json={"task_type": "auth_probe", "payload": {}}),
    RouteCase("get", f"/tasks/{UNKNOWN_ID}", "producer", 404),
    RouteCase("get", f"/artifacts/{UNKNOWN_ID}", "producer", 404),
    RouteCase("post", "/workers/register", "worker", 200, json={
        "worker_id": "auth-test-worker", "name": "Auth Test Worker",
        "session_id": "session-1", "capabilities": {"task_types": ["auth_probe"]},
    }),
    RouteCase("post", "/workers/missing-worker/heartbeat", "worker", 409, json={
        "session_id": "session-1", "state": "idle",
    }),
    RouteCase("get", "/tasks/next", "worker", 204,
              params={"task_types": "nothing_waiting", "worker_id": "auth-test-worker"}),
    RouteCase("post", f"/tasks/{UNKNOWN_ID}/complete", "worker", 409,
              json={"claim_token": "missing", "result": {}}),
    RouteCase("post", f"/tasks/{UNKNOWN_ID}/fail", "worker", 409,
              json={"claim_token": "missing", "error": "probe"}),
    RouteCase("post", "/artifacts", "worker", 200,
              files={"file": ("probe.txt", b"auth probe", "text/plain")}),
)


def _request(client: TestClient, prefix: str, case: RouteCase, key: str | None):
    headers = {"X-API-Key": key} if key else {}
    return client.request(
        case.method,
        f"{prefix}{case.path}",
        headers=headers,
        json=case.json,
        params=case.params,
        files=case.files,
    )


@pytest.mark.parametrize("prefix", ["/api", "/api/queue"])
@pytest.mark.parametrize("case", CASES, ids=lambda case: f"{case.method}-{case.path}")
def test_machine_route_key_matrix(client, prefix, case):
    right_key = PRODUCER_KEY if case.role == "producer" else WORKER_KEY
    wrong_role_key = WORKER_KEY if case.role == "producer" else PRODUCER_KEY

    assert _request(client, prefix, case, None).status_code == 401
    assert _request(client, prefix, case, wrong_role_key).status_code == 401
    assert _request(client, prefix, case, right_key).status_code == case.expected


@pytest.mark.parametrize("prefix", ["/api", "/api/queue"])
def test_human_bearer_session_does_not_authorize_worker_routes(
    client, prefix, monkeypatch,
):
    async def authenticated_admin(_token):
        return {"id": "admin-1", "email": "admin@example.com"}

    monkeypatch.setattr(auth, "_fetch_supabase_user", authenticated_admin)
    response = client.post(
        f"{prefix}/workers/register",
        headers={"Authorization": "Bearer valid-human-session"},
        json={
            "worker_id": "human-session-test", "name": "Human Session Test",
            "session_id": "session-1", "capabilities": {},
        },
    )
    assert response.status_code == 401


def test_rollout_mode_accepts_missing_key_but_rejects_a_present_invalid_key(
    client, monkeypatch,
):
    monkeypatch.setattr(auth.settings, "machine_auth_required", False)
    body = {"task_type": "rollout_probe", "payload": {}}

    assert client.post("/api/tasks", json=body).status_code == 200
    assert client.post(
        "/api/tasks", json=body, headers={"X-API-Key": "invalid"},
    ).status_code == 401
