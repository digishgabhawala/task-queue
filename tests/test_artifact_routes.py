import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app import auth, storage
from app.main import app
from app.models import Artifact

PRODUCER_KEY = "tq_test_producer_0123456789"
PRODUCER_HASH = hashlib.sha256(PRODUCER_KEY.encode()).hexdigest()


@pytest.fixture(autouse=True)
def _producer_key(monkeypatch):
    monkeypatch.setattr(auth.settings, "service_api_keys", f"test-producer:{PRODUCER_HASH}")
    monkeypatch.setattr(auth.settings, "machine_auth_required", False)


@pytest.fixture()
def client():
    return TestClient(app)


def test_delete_artifact_requires_key_even_when_rollout_off(client):
    response = client.delete(f"/api/artifacts/{uuid.uuid4()}")

    assert response.status_code == 401


def test_delete_artifact_rejects_invalid_key(client):
    response = client.delete(
        f"/api/artifacts/{uuid.uuid4()}",
        headers={"X-API-Key": "invalid"},
    )

    assert response.status_code == 401


def test_delete_artifact_rejects_non_uuid(client):
    response = client.delete(
        "/api/artifacts/not-a-uuid",
        headers={"X-API-Key": PRODUCER_KEY},
    )

    assert response.status_code == 404


def test_delete_artifact_deletes_three_extensions_and_row(
    client, db_session, monkeypatch,
):
    artifact_id = str(uuid.uuid4())
    artifact = Artifact(
        id=artifact_id,
        content_type="image/png",
        size_bytes=10,
        storage_path=f"{artifact_id}.png",
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    db_session.add(artifact)
    db_session.commit()
    deleted_paths = []
    monkeypatch.setattr(storage, "delete", deleted_paths.append)

    response = client.delete(
        f"/api/artifacts/{artifact_id}",
        headers={"X-API-Key": PRODUCER_KEY},
    )

    assert response.status_code == 204
    assert set(deleted_paths) == {
        f"{artifact_id}.png",
        f"{artifact_id}.webp",
        f"{artifact_id}.jpg",
    }
    assert len(deleted_paths) == 3
    db_session.expire_all()
    assert db_session.get(Artifact, artifact_id) is None


def test_delete_artifact_missing_row_and_files_is_204(client, monkeypatch):
    artifact_id = str(uuid.uuid4())

    def missing(_path):
        response = httpx.Response(
            404,
            request=httpx.Request("DELETE", "https://storage.test/artifact"),
        )
        raise httpx.HTTPStatusError(
            "not found", request=response.request, response=response,
        )

    monkeypatch.setattr(storage, "delete", missing)

    response = client.delete(
        f"/api/artifacts/{artifact_id}",
        headers={"X-API-Key": PRODUCER_KEY},
    )

    assert response.status_code == 204
