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


def _fake_forge2(monkeypatch, script):
    """Point the worker at a tiny real process instead of forge2."""
    import sys

    import workers.image_worker as worker

    monkeypatch.setattr(worker, "_build_real_command",
                        lambda *args: [sys.executable, "-c", script, "out.png"])
    return worker


CORRUPT_LINE = ("corrupt_output: near-uniform image (max channel stddev 0.00); "
                "saved as queue_ab12.png")


def test_corrupt_render_carries_forge2_details(monkeypatch, capsys):
    worker = _fake_forge2(monkeypatch, (
        "import sys\n"
        "print('forge2 progress log', file=sys.stderr)\n"
        f"print({CORRUPT_LINE!r}, file=sys.stderr)\n"
        "sys.exit(3)\n"))
    with pytest.raises(RuntimeError) as err:
        worker._run_real("gtee_dev", "waving", 42, "full")
    assert str(err.value) == CORRUPT_LINE
    assert "forge2 progress log" in capsys.readouterr().err  # logs still streamed live


def test_corrupt_exit_without_message_still_labelled_corrupt(monkeypatch):
    worker = _fake_forge2(monkeypatch, "import sys; sys.exit(3)")
    with pytest.raises(RuntimeError, match=r"^corrupt_output: .*forge2 exit 3"):
        worker._run_real("gtee_dev", "waving", 42, "full")


def test_other_forge2_failures_are_not_labelled_corrupt(monkeypatch):
    import subprocess

    worker = _fake_forge2(monkeypatch, "import sys; sys.exit(1)")
    with pytest.raises(subprocess.CalledProcessError):
        worker._run_real("gtee_dev", "waving", 42, "full")


class FakeSupervisor:
    def __init__(self, managed=False):
        self.calls = []
        self.managed = managed

    def stop(self):
        self.calls.append("stop")
        self.managed = False

    def request_restart(self):
        self.calls.append("request_restart")

    def ensure_running(self):
        self.calls.append("ensure_running")

    def mark_busy(self):
        self.calls.append("busy")

    def mark_idle(self):
        self.calls.append("idle")

    def restart(self):
        self.calls.append("restart")
        return True

    def details(self):
        return {"state": "ready", "managed": True}


class StageRecorder:
    def __init__(self):
        self.stages = []

    def update(self, *, state, task=None, stage=None, message=None):
        self.stages.append(stage)


class UploadClient:
    def upload_artifact(self, content, content_type, filename):
        return {"id": "artifact-1"}


TASK = {"id": "t1", "claim_token": "c",
        "payload": {"character_id": "gtee_dev", "task": "waving", "seed": 1, "profile": "full"}}


def _real_mode(monkeypatch, run_real=None, shortfall=None):
    import workers.image_worker as worker

    ran = []
    monkeypatch.setattr(worker, "MOCK_MODE", False)
    monkeypatch.setattr(worker.memory, "memory_shortfall", lambda profile: shortfall)
    monkeypatch.setattr(worker, "_run_real",
                        run_real or (lambda *args: ran.append(args) or b"png"))
    return worker, ran


def test_full_render_refused_when_memory_is_short(monkeypatch):
    worker, ran = _real_mode(
        monkeypatch, shortfall="insufficient_memory: full quality needs 14 GB free")
    sup = FakeSupervisor()
    with pytest.raises(RuntimeError, match=r"^insufficient_memory:"):
        worker.handle_task(UploadClient(), TASK, StageRecorder(), sup)
    assert ran == []
    assert "ensure_running" not in sup.calls


def test_comfyui_is_started_before_rendering_and_released_after(monkeypatch):
    worker, ran = _real_mode(monkeypatch)
    sup, hb = FakeSupervisor(), StageRecorder()
    assert worker.handle_task(UploadClient(), TASK, hb, sup) == {"image_artifact_id": "artifact-1"}
    assert sup.calls == ["busy", "ensure_running", "idle"]
    assert hb.stages[:2] == ["starting_comfyui", "rendering"]
    assert len(ran) == 1


def test_corrupt_render_schedules_comfyui_restart_and_still_fails(monkeypatch):
    def corrupt(*args):
        raise RuntimeError("corrupt_output: ComfyUI returned a blank/uniform image")

    worker, _ = _real_mode(monkeypatch, run_real=corrupt)
    sup = FakeSupervisor()
    with pytest.raises(RuntimeError, match=r"^corrupt_output:"):
        worker.handle_task(UploadClient(), TASK, StageRecorder(), sup)
    assert "request_restart" in sup.calls
    assert "restart" not in sup.calls  # fail_task must not wait for a restart
    assert sup.calls[-1] == "idle"


def test_other_render_failures_do_not_restart_comfyui(monkeypatch):
    import subprocess

    def crash(*args):
        raise subprocess.CalledProcessError(1, "forge2")

    worker, _ = _real_mode(monkeypatch, run_real=crash)
    sup = FakeSupervisor()
    with pytest.raises(subprocess.CalledProcessError):
        worker.handle_task(UploadClient(), TASK, StageRecorder(), sup)
    assert "restart" not in sup.calls and "request_restart" not in sup.calls


def test_memory_gate_frees_idle_worker_comfyui_before_refusing(monkeypatch):
    import workers.image_worker as worker

    answers = iter(["insufficient_memory: needs 14 GB", None])
    worker_mod, ran = _real_mode(monkeypatch)
    monkeypatch.setattr(worker.memory, "memory_shortfall", lambda profile: next(answers))
    sup = FakeSupervisor(managed=True)
    worker_mod.handle_task(UploadClient(), TASK, StageRecorder(), sup)
    assert sup.calls[0] == "stop"
    assert len(ran) == 1


def test_memory_gate_never_stops_user_started_comfyui(monkeypatch):
    worker, ran = _real_mode(monkeypatch, shortfall="insufficient_memory: needs 14 GB")
    sup = FakeSupervisor(managed=False)
    with pytest.raises(RuntimeError, match=r"^insufficient_memory:"):
        worker.handle_task(UploadClient(), TASK, StageRecorder(), sup)
    assert "stop" not in sup.calls
    assert ran == []


def test_sighup_is_handled_like_sigterm():
    import signal

    import workers.image_worker as worker

    assert signal.SIGHUP in worker.SHUTDOWN_SIGNALS
    assert signal.SIGTERM in worker.SHUTDOWN_SIGNALS


def test_mock_mode_skips_memory_gate(monkeypatch):
    import workers.image_worker as worker

    monkeypatch.setattr(worker, "MOCK_MODE", True)
    monkeypatch.setattr(worker, "MOCK_DELAY_SECONDS", 0)
    monkeypatch.setattr(worker, "_run_mock", lambda character_id: b"png")
    monkeypatch.setattr(worker.memory, "memory_shortfall",
                        lambda profile: pytest.fail("mock mode must not gate on memory"))
    assert worker.handle_task(UploadClient(), TASK) == {"image_artifact_id": "artifact-1"}


def test_heartbeat_carries_worker_details(monkeypatch):
    import workers.image_worker as worker

    sent = {}

    class Client:
        def heartbeat(self, *args, **kwargs):
            sent.update(kwargs)

    reporter = worker.WorkerHeartbeat(Client(), details=lambda: {"comfyui": {"state": "ready"}})
    reporter._send()
    assert sent["details"] == {"comfyui": {"state": "ready"}}


def test_heartbeat_survives_failing_details_provider(monkeypatch):
    import workers.image_worker as worker

    sent = {}

    class Client:
        def heartbeat(self, *args, **kwargs):
            sent.update(kwargs)

    def broken():
        raise RuntimeError("vm_stat exploded")

    worker.WorkerHeartbeat(Client(), details=broken)._send()
    assert sent["state"] == "idle"
    assert sent.get("details") is None
