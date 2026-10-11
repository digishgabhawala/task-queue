import pytest

from workers.image_worker import WorkerHeartbeat, _build_real_command


def test_real_command_forwards_explicit_profile():
    command = _build_real_command("gtee_dev", "turning a dial", 42, "lightning")
    assert command[command.index("--profile") + 1] == "lightning"
    assert command[command.index("--seed") + 1] == "42"


def test_real_command_rejects_unknown_profile():
    with pytest.raises(ValueError, match="unsupported image profile"):
        _build_real_command("gtee_dev", "turning a dial", 42, "turbo")


def test_worker_heartbeat_reports_registration_and_progress(monkeypatch):
    monkeypatch.setattr("workers.image_worker.WORKER_ID", "worker-1")
    monkeypatch.setattr("workers.image_worker.WORKER_NAME", "Test Mac")
    monkeypatch.setattr("workers.image_worker.WORKER_SESSION_ID", "session-1")

    class FakeClient:
        def __init__(self):
            self.registered = None
            self.last_heartbeat = None

        def register_worker(self, *args, **kwargs):
            self.registered = (args, kwargs)

        def heartbeat(self, *args, **kwargs):
            self.last_heartbeat = (args, kwargs)

    client = FakeClient()
    reporter = WorkerHeartbeat(client)
    reporter.register()
    task = {"id": "task-1", "claim_token": "token-1"}
    reporter.update(
        state="busy", task=task, stage="rendering", message="Generating image")
    reporter._send()

    assert client.registered[0][:3] == ("worker-1", "Test Mac", "session-1")
    assert client.last_heartbeat[1]["current_task_id"] == "task-1"
    assert client.last_heartbeat[1]["progress_stage"] == "rendering"
