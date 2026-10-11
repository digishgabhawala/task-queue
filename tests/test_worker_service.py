from datetime import timedelta

import pytest

from app.services import task_service as tsvc
from app.services import worker_service as wsvc


def _register(db_session, worker_id="worker-1", session_id="session-1"):
    return wsvc.register_worker(
        db_session,
        worker_id=worker_id,
        name="Test Mac",
        session_id=session_id,
        capabilities={"task_types": ["image_generation"]},
        version="test",
    )


def test_register_and_list_worker(db_session):
    worker = _register(db_session)
    assert worker.state == "idle"
    assert wsvc.is_online(worker)
    assert wsvc.list_workers(db_session)[0].id == "worker-1"


def test_reregister_replaces_session(db_session):
    _register(db_session)
    worker = _register(db_session, session_id="session-2")
    assert worker.session_id == "session-2"
    with pytest.raises(wsvc.WorkerServiceError, match="newer process"):
        wsvc.heartbeat(
            db_session, worker_id="worker-1", session_id="session-1",
            state="idle", current_task_id=None, claim_token=None,
            progress_stage=None, progress_message=None,
            progress_current=None, progress_total=None)


def test_busy_heartbeat_updates_progress_and_renews_lease(db_session):
    _register(db_session)
    task = tsvc.submit_task(
        db_session, "image_generation", {"seed": 42}, lease_minutes=240)
    claimed = tsvc.claim_next(db_session, ["image_generation"], "worker-1")

    worker = wsvc.heartbeat(
        db_session, worker_id="worker-1", session_id="session-1",
        state="busy", current_task_id=task.id,
        claim_token=claimed["claim_token"], progress_stage="rendering",
        progress_message="Generating image", progress_current=None,
        progress_total=None)

    db_session.refresh(task)
    assert worker.current_task_id == task.id
    assert task.progress_stage == "rendering"
    remaining = task.lease_expires_at - wsvc._now()
    assert timedelta(minutes=4, seconds=50) < remaining <= timedelta(minutes=5)


def test_heartbeat_rejects_task_owned_by_another_worker(db_session):
    _register(db_session)
    task = tsvc.submit_task(db_session, "image_generation", {"seed": 42})
    claimed = tsvc.claim_next(db_session, ["image_generation"], "worker-2")

    with pytest.raises(wsvc.WorkerServiceError, match="does not own"):
        wsvc.heartbeat(
            db_session, worker_id="worker-1", session_id="session-1",
            state="busy", current_task_id=task.id,
            claim_token=claimed["claim_token"], progress_stage="rendering",
            progress_message=None, progress_current=None, progress_total=None)


def test_worker_becomes_offline_after_two_minutes(db_session):
    worker = _register(db_session)
    assert wsvc.is_online(
        worker, now=worker.last_heartbeat_at + timedelta(seconds=119))
    assert not wsvc.is_online(
        worker, now=worker.last_heartbeat_at + timedelta(seconds=121))
