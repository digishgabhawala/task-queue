import uuid

import pytest

from app.services import artifact_service as asvc


def test_upload_and_get_artifact(db_session):
    content = b"fake png bytes for testing"
    artifact = asvc.upload_artifact(db_session, content, "image/png")

    assert artifact.content_type == "image/png"
    assert artifact.size_bytes == len(content)
    assert artifact.storage_path.endswith(".png")

    fetched = asvc.get_artifact(db_session, artifact.id)
    assert fetched.id == artifact.id


def test_upload_and_download_roundtrip(db_session):
    content = b"round trip me"
    artifact = asvc.upload_artifact(db_session, content, "image/png")

    downloaded, content_type = asvc.get_artifact_bytes(db_session, artifact.id)
    assert downloaded == content
    assert content_type == "image/png"


def test_get_artifact_malformed_id_raises(db_session):
    with pytest.raises(asvc.ArtifactServiceError):
        asvc.get_artifact(db_session, "does-not-exist")


def test_get_artifact_valid_but_missing_id_raises(db_session):
    with pytest.raises(asvc.ArtifactServiceError):
        asvc.get_artifact(db_session, str(uuid.uuid4()))
