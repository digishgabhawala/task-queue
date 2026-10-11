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


def test_mock_mode_honors_mock_delay(monkeypatch):
    import workers.image_worker as worker

    slept = []
    monkeypatch.setattr(worker, "MOCK_MODE", True)
    monkeypatch.setattr(worker, "MOCK_DELAY_SECONDS", 7.0)
    monkeypatch.setattr(worker.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(worker, "_run_mock", lambda character_id: b"png")

    class Client:
        def upload_artifact(self, content, content_type, filename):
            return {"id": "artifact-1"}

    result = worker.handle_task(
        Client(), {"id": "t1", "claim_token": "c", "payload": {"character_id": "gtee_dev"}})

    assert slept == [7.0]
    assert result == {"image_artifact_id": "artifact-1"}


def _fail_forge2(monkeypatch, returncode):
    import subprocess

    import workers.image_worker as worker

    def run(cmd, **kwargs):
        raise subprocess.CalledProcessError(returncode, cmd)

    monkeypatch.setattr(worker.subprocess, "run", run)
    return worker


def test_corrupt_render_exit_code_becomes_corrupt_output_error(monkeypatch):
    worker = _fail_forge2(monkeypatch, 3)
    with pytest.raises(RuntimeError, match=r"^corrupt_output:"):
        worker._run_real("gtee_dev", "waving", 42, "full")


def test_other_forge2_failures_are_not_labelled_corrupt(monkeypatch):
    import subprocess

    worker = _fail_forge2(monkeypatch, 1)
    with pytest.raises(subprocess.CalledProcessError):
        worker._run_real("gtee_dev", "waving", 42, "full")
