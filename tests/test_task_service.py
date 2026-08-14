import uuid

import pytest

from app.services import task_service as tsvc


def test_submit_task_creates_queued_task(db_session):
    task = tsvc.submit_task(db_session, "text_generation", {"prompt": "hello"})
    assert task.status == "queued"
    assert task.task_type == "text_generation"
    assert task.payload == {"prompt": "hello"}
    assert task.expires_at is not None


def test_claim_next_returns_none_when_nothing_queued(db_session):
    assert tsvc.claim_next(db_session, ["image_generation"], "worker-1") is None


def test_claim_next_claims_matching_task_type(db_session):
    task = tsvc.submit_task(db_session, "image_generation", {"seed": 42})
    claimed = tsvc.claim_next(db_session, ["image_generation"], "worker-1")
    assert claimed is not None
    assert claimed["id"] == task.id
    assert claimed["status"] == "claimed"
    assert claimed["claimed_by"] == "worker-1"
    assert claimed["claim_token"]


def test_claim_next_ignores_non_matching_task_type(db_session):
    tsvc.submit_task(db_session, "text_generation", {"prompt": "hi"})
    assert tsvc.claim_next(db_session, ["image_generation"], "worker-1") is None


def test_claim_next_does_not_double_claim(db_session):
    tsvc.submit_task(db_session, "image_generation", {"seed": 1})
    first = tsvc.claim_next(db_session, ["image_generation"], "worker-1")
    second = tsvc.claim_next(db_session, ["image_generation"], "worker-2")
    assert first is not None
    assert second is None  # only one task existed, already claimed


def test_claim_next_multiple_tasks_claims_oldest_first(db_session):
    first_task = tsvc.submit_task(db_session, "image_generation", {"n": 1})
    tsvc.submit_task(db_session, "image_generation", {"n": 2})
    claimed = tsvc.claim_next(db_session, ["image_generation"], "worker-1")
    assert claimed["id"] == first_task.id


def test_complete_task_with_correct_token(db_session):
    task = tsvc.submit_task(db_session, "image_generation", {"seed": 1})
    claimed = tsvc.claim_next(db_session, ["image_generation"], "worker-1")
    done = tsvc.complete_task(db_session, task.id, claimed["claim_token"], {"artifact_id": "abc"})
    assert done.status == "done"
    assert done.result == {"artifact_id": "abc"}
    assert done.completed_at is not None


def test_complete_task_with_wrong_token_raises(db_session):
    task = tsvc.submit_task(db_session, "image_generation", {"seed": 1})
    tsvc.claim_next(db_session, ["image_generation"], "worker-1")
    with pytest.raises(tsvc.TaskServiceError):
        tsvc.complete_task(db_session, task.id, "wrong-token", {})


def test_complete_task_not_claimed_raises(db_session):
    task = tsvc.submit_task(db_session, "image_generation", {"seed": 1})
    with pytest.raises(tsvc.TaskServiceError):
        tsvc.complete_task(db_session, task.id, "any-token", {})


def test_fail_task_with_correct_token(db_session):
    task = tsvc.submit_task(db_session, "image_generation", {"seed": 1})
    claimed = tsvc.claim_next(db_session, ["image_generation"], "worker-1")
    failed = tsvc.fail_task(db_session, task.id, claimed["claim_token"], "ComfyUI unreachable")
    assert failed.status == "failed"
    assert failed.error == "ComfyUI unreachable"


def test_expired_lease_is_reclaimed(db_session):
    task = tsvc.submit_task(db_session, "image_generation", {"seed": 1})
    # negative lease -> already expired the instant it's claimed
    tsvc.claim_next(db_session, ["image_generation"], "worker-1", lease_minutes=-1)
    reclaimed = tsvc.claim_next(db_session, ["image_generation"], "worker-2")
    assert reclaimed is not None
    assert reclaimed["id"] == task.id
    assert reclaimed["claimed_by"] == "worker-2"


def test_completing_after_reclaim_with_stale_token_fails(db_session):
    """The original worker's claim_token must not still work once the task
    has been reclaimed by someone else -- this is exactly what claim_token
    verification exists to prevent."""
    task = tsvc.submit_task(db_session, "image_generation", {"seed": 1})
    stale_claim = tsvc.claim_next(db_session, ["image_generation"], "worker-1", lease_minutes=-1)
    tsvc.claim_next(db_session, ["image_generation"], "worker-2")  # reclaims it

    with pytest.raises(tsvc.TaskServiceError):
        tsvc.complete_task(db_session, task.id, stale_claim["claim_token"], {})


def test_get_task_malformed_id_raises(db_session):
    with pytest.raises(tsvc.TaskServiceError):
        tsvc.get_task(db_session, "does-not-exist")


def test_get_task_valid_but_missing_id_raises(db_session):
    with pytest.raises(tsvc.TaskServiceError):
        tsvc.get_task(db_session, str(uuid.uuid4()))


def test_list_tasks_filters_by_status_and_type(db_session):
    tsvc.submit_task(db_session, "image_generation", {"n": 1})
    t2 = tsvc.submit_task(db_session, "text_generation", {"n": 2})
    tsvc.claim_next(db_session, ["text_generation"], "worker-1")

    assert len(tsvc.list_tasks(db_session, task_type="image_generation")) == 1
    claimed_list = tsvc.list_tasks(db_session, status="claimed")
    assert len(claimed_list) == 1
    assert claimed_list[0].id == t2.id
