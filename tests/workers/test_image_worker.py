import io
import json
from pathlib import Path

import pytest
from PIL import Image

from workers.image_worker import (
    WorkerHeartbeat,
    _build_hero_candidate_command,
    _build_real_command,
)


def _png(size=(64, 64), color=(30, 120, 200)) -> bytes:
    """A real PNG: post renders are converted, so fakes must decode."""
    buffer = io.BytesIO()
    image = Image.new("RGB", size, color)
    # A thin dark outline, like the flat cartoon renders' line art.
    for x in range(size[0]):
        image.putpixel((x, size[1] // 2), (0, 0, 0))
    image.save(buffer, "PNG")
    return buffer.getvalue()


RENDER_PNG = _png()


def test_real_command_forwards_explicit_profile():
    command = _build_real_command("gtee_dev", "turning a dial", 42, "lightning")
    assert command[command.index("--profile") + 1] == "lightning"
    assert command[command.index("--seed") + 1] == "42"


def test_real_command_forwards_explicit_remote_files(tmp_path):
    command = _build_real_command(
        "gtee_dev", "turning a dial", 42, "full",
        hero_path=tmp_path / "hero.png",
        card_path=tmp_path / "character.json",
        output_path=tmp_path / "result.png",
    )
    assert command[command.index("--hero-path") + 1] == str(tmp_path / "hero.png")
    assert command[command.index("--card-path") + 1] == str(tmp_path / "character.json")
    assert command[command.index("--output-path") + 1] == str(tmp_path / "result.png")


def test_real_command_rejects_unknown_profile():
    with pytest.raises(ValueError, match="unsupported image profile"):
        _build_real_command("gtee_dev", "turning a dial", 42, "turbo")


def test_hero_candidate_command_forwards_card_seed_profile_and_output(tmp_path):
    card = tmp_path / "character.json"
    output = tmp_path / "candidate.png"
    command = _build_hero_candidate_command(card, 1701, "lightning", output)

    assert command[3] == "hero-candidate"
    assert command[4] == str(card)
    assert command[command.index("--seed") + 1] == "1701"
    assert command[command.index("--profile") + 1] == "lightning"
    assert command[command.index("--output-path") + 1] == str(output)


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
    monkeypatch.setattr(worker, "_run_mock", lambda character_id: RENDER_PNG)

    class Client:
        def upload_artifact(self, content, content_type, filename):
            return {"id": "artifact-1"}

    result = worker.handle_task(
        Client(), {"id": "t1", "claim_token": "c", "payload": {"character_id": "gtee_dev"}})

    assert slept == [7.0]
    assert result == {"image_artifact_id": "artifact-1"}


SUPABASE_HERO_URL = ("https://proj.supabase.co/storage/v1/object/public/"
                     "characters/u/gtee_dev/hero.png?v=abc")


def _hero_payload(url=SUPABASE_HERO_URL):
    return {
        "hero_image_url": url,
        "hero_asset_id": "sha256:abc",
        "hero_version": 2,
        "character_card": {"character_id": "gtee_dev"},
    }


def _fake_hero_server(monkeypatch, tmp_path, body=None, chunks=None, headers=None):
    """Point the worker's streamed download at canned bytes; record calls."""
    from contextlib import contextmanager

    import httpx

    import workers.image_worker as worker

    calls, consumed = [], []
    monkeypatch.setattr(worker, "HERO_CACHE_DIR", tmp_path)
    monkeypatch.setattr(worker, "SUPABASE_URL", "https://proj.supabase.co")
    monkeypatch.setattr(worker, "HERO_ALLOWED_HOSTS", "")
    monkeypatch.setattr(worker.httpx, "get", lambda *a, **k: pytest.fail("must stream"))

    def produce():
        for chunk in chunks:
            consumed.append(len(chunk))
            yield chunk

    @contextmanager
    def stream(method, url, **kwargs):
        calls.append((method, url, kwargs))
        content = produce() if chunks is not None else body
        yield httpx.Response(200, content=content, headers=headers or {},
                             request=httpx.Request(method, url))

    monkeypatch.setattr(worker.httpx, "stream", stream)
    return worker, calls, consumed


def test_remote_hero_is_downloaded_once_and_cached(monkeypatch, tmp_path):
    worker, calls, _ = _fake_hero_server(monkeypatch, tmp_path, body=RENDER_PNG)

    first = worker._resolve_remote_hero(_hero_payload())
    second = worker._resolve_remote_hero(_hero_payload())

    assert first == second and first.read_bytes() == RENDER_PNG
    assert [c[1] for c in calls] == [SUPABASE_HERO_URL]
    assert calls[0][2].get("follow_redirects") is False


@pytest.mark.parametrize("fmt", ["JPEG", "WEBP"])
def test_remote_hero_accepts_jpeg_and_webp(monkeypatch, tmp_path, fmt):
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 10, 10)).save(buffer, fmt)
    worker, _, _ = _fake_hero_server(monkeypatch, tmp_path, body=buffer.getvalue())

    assert worker._resolve_remote_hero(_hero_payload()).read_bytes() == buffer.getvalue()


def test_remote_hero_from_another_host_is_refused(monkeypatch, tmp_path):
    worker, calls, _ = _fake_hero_server(monkeypatch, tmp_path, body=RENDER_PNG)

    with pytest.raises(ValueError, match="not an allowed hero host"):
        worker._resolve_remote_hero(_hero_payload("https://evil.example/hero.png"))
    assert calls == [] and list(tmp_path.iterdir()) == []


def test_remote_hero_lookalike_host_is_refused(monkeypatch, tmp_path):
    worker, calls, _ = _fake_hero_server(monkeypatch, tmp_path, body=RENDER_PNG)

    with pytest.raises(ValueError, match="not an allowed hero host"):
        worker._resolve_remote_hero(
            _hero_payload("https://proj.supabase.co.evil.example/hero.png"))
    assert calls == []


def test_remote_hero_over_http_is_refused(monkeypatch, tmp_path):
    worker, calls, _ = _fake_hero_server(monkeypatch, tmp_path, body=RENDER_PNG)

    with pytest.raises(ValueError, match="https"):
        worker._resolve_remote_hero(_hero_payload(SUPABASE_HERO_URL.replace("https", "http")))
    assert calls == []


def test_remote_hero_extra_allowed_host_is_accepted(monkeypatch, tmp_path):
    worker, calls, _ = _fake_hero_server(monkeypatch, tmp_path, body=RENDER_PNG)
    monkeypatch.setattr(worker, "HERO_ALLOWED_HOSTS", " cdn.example.com , other.example ")

    worker._resolve_remote_hero(_hero_payload("https://cdn.example.com/hero.png"))
    assert len(calls) == 1


def test_remote_hero_refused_when_no_host_is_configured(monkeypatch, tmp_path):
    worker, calls, _ = _fake_hero_server(monkeypatch, tmp_path, body=RENDER_PNG)
    monkeypatch.setattr(worker, "SUPABASE_URL", None)

    with pytest.raises(ValueError, match="not an allowed hero host"):
        worker._resolve_remote_hero(_hero_payload())
    assert calls == []


def test_oversize_remote_hero_download_stops_early(monkeypatch, tmp_path):
    megabyte = b"x" * (1024 * 1024)
    worker, _, consumed = _fake_hero_server(
        monkeypatch, tmp_path, chunks=[megabyte] * 50)
    monkeypatch.setattr(worker, "MAX_HERO_BYTES", 3 * 1024 * 1024)

    with pytest.raises(ValueError, match="exceeds"):
        worker._resolve_remote_hero(_hero_payload())
    assert len(consumed) <= 5  # stopped long before all 50 MB arrived
    assert list(tmp_path.iterdir()) == []


def test_remote_hero_declared_oversize_is_refused_before_reading(monkeypatch, tmp_path):
    worker, _, consumed = _fake_hero_server(
        monkeypatch, tmp_path, chunks=[b"x" * 1024],
        headers={"Content-Length": str(100 * 1024 * 1024)})

    with pytest.raises(ValueError, match="exceeds"):
        worker._resolve_remote_hero(_hero_payload())
    assert consumed == []


def test_non_image_remote_hero_is_refused(monkeypatch, tmp_path):
    worker, _, _ = _fake_hero_server(monkeypatch, tmp_path, body=b"<html>not an image</html>")

    with pytest.raises(ValueError, match="not a PNG, JPEG or WebP"):
        worker._resolve_remote_hero(_hero_payload())
    assert list(tmp_path.iterdir()) == []


def test_unsupported_image_remote_hero_is_refused(monkeypatch, tmp_path):
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buffer, "GIF")
    worker, _, _ = _fake_hero_server(monkeypatch, tmp_path, body=buffer.getvalue())

    with pytest.raises(ValueError, match="not a PNG, JPEG or WebP"):
        worker._resolve_remote_hero(_hero_payload())


def test_remote_hero_requires_complete_character_card():
    import workers.image_worker as worker

    with pytest.raises(ValueError, match="hero_image_url and character_card"):
        worker._resolve_remote_hero({"hero_image_url": "https://storage.example/hero.png"})


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


def test_remote_run_uses_task_temporary_files_and_cleans_them(monkeypatch, tmp_path):
    import workers.image_worker as worker

    hero = tmp_path / "cached-hero.png"
    hero.write_bytes(b"hero")
    observed = {}

    def run(command, timeout):
        card_path = command[command.index("--card-path") + 1]
        output_path = command[command.index("--output-path") + 1]
        observed["card_path"] = card_path
        observed["output_path"] = output_path
        observed["card"] = json.loads(Path(card_path).read_text())
        Path(output_path).write_bytes(b"rendered")
        return 0, []

    monkeypatch.setattr(worker, "_run_forge2", run)
    result = worker._run_real(
        "gtee_dev", "waving", 42, "full", hero,
        {"character_id": "gtee_dev", "trigger_token": "gtee_dev"},
    )

    assert result == b"rendered"
    assert observed["card"]["character_id"] == "gtee_dev"
    assert not Path(observed["card_path"]).exists()
    assert not Path(observed["output_path"]).exists()


def test_hero_candidate_run_uses_temporary_files_and_reads_sidecar(monkeypatch):
    import workers.image_worker as worker

    observed = {}

    def run(command, timeout):
        card_path = Path(command[4])
        output_path = Path(command[command.index("--output-path") + 1])
        observed["card_path"] = card_path
        observed["output_path"] = output_path
        observed["card"] = json.loads(card_path.read_text())
        output_path.write_bytes(b"rendered-candidate")
        output_path.with_suffix(".json").write_text(json.dumps({
            "filename": "candidate.png",
            "kind": "hero_candidate",
            "character_id": "gtee_dev",
            "seed": 1701,
            "prompt": "a developer mascot",
        }))
        return 0, []

    monkeypatch.setattr(worker, "_run_forge2", run)
    image, metadata = worker._run_hero_candidate(
        {"character_id": "gtee_dev", "trigger_token": "gtee_dev"},
        1701, "full")

    assert image == b"rendered-candidate"
    assert metadata["seed"] == 1701
    assert observed["card"]["character_id"] == "gtee_dev"
    assert not observed["card_path"].exists()
    assert not observed["output_path"].exists()


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
HERO_TASK = {"id": "h1", "claim_token": "c", "payload": {
    "character_card": {"character_id": "gtee_dev", "trigger_token": "gtee_dev"},
    "seed": 1701,
    "profile": "full",
}}


def _real_mode(monkeypatch, run_real=None, shortfall=None):
    import workers.image_worker as worker

    ran = []
    monkeypatch.setattr(worker, "MOCK_MODE", False)
    monkeypatch.setattr(worker.memory, "memory_shortfall", lambda profile: shortfall)
    monkeypatch.setattr(worker, "_run_real",
                        run_real or (lambda *args: ran.append(args) or RENDER_PNG))
    return worker, ran


def test_remote_render_passes_cached_hero_and_card_to_forge(monkeypatch, tmp_path):
    worker, ran = _real_mode(monkeypatch)
    hero = tmp_path / "hero.png"
    hero.write_bytes(b"png")
    monkeypatch.setattr(worker, "_resolve_remote_hero", lambda payload: hero)
    remote = {**TASK, "payload": {**TASK["payload"],
              "hero_image_url": "https://storage.example/hero.png",
              "hero_asset_id": "abc", "hero_version": 1,
              "character_card": {"character_id": "gtee_dev", "trigger_token": "gtee_dev"}}}

    worker.handle_task(UploadClient(), remote, StageRecorder(), FakeSupervisor())

    assert ran[0][4] == hero
    assert ran[0][5]["character_id"] == "gtee_dev"


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


def test_hero_candidate_uses_comfyui_lifecycle_and_returns_metadata(monkeypatch):
    import workers.image_worker as worker

    monkeypatch.setattr(worker, "MOCK_MODE", False)
    monkeypatch.setattr(worker.memory, "memory_shortfall", lambda profile: None)
    monkeypatch.setattr(worker, "_run_hero_candidate", lambda card, seed, profile: (
        b"png", {"character_id": card["character_id"], "seed": seed,
                 "model_id": "fake-t2i"}))
    sup, hb = FakeSupervisor(), StageRecorder()

    result = worker.handle_hero_candidate_task(UploadClient(), HERO_TASK, hb, sup)

    assert result == {
        "hero_candidate_artifact_id": "artifact-1",
        "candidate": {"character_id": "gtee_dev", "seed": 1701,
                      "model_id": "fake-t2i", "filename": "h1.png"},
    }
    assert sup.calls == ["busy", "ensure_running", "idle"]
    assert hb.stages == ["starting_comfyui", "generating_hero", "uploading"]


def test_mock_hero_candidate_uploads_valid_png_and_metadata(monkeypatch):
    import workers.image_worker as worker

    uploaded = {}

    class Client:
        def upload_artifact(self, content, content_type, filename):
            uploaded.update(content=content, content_type=content_type, filename=filename)
            return {"id": "hero-artifact"}

    monkeypatch.setattr(worker, "MOCK_MODE", True)
    monkeypatch.setattr(worker, "MOCK_DELAY_SECONDS", 0)
    result = worker.handle_hero_candidate_task(Client(), HERO_TASK)

    assert uploaded["content"].startswith(b"\x89PNG\r\n\x1a\n")
    assert uploaded["content_type"] == "image/png"
    assert result["hero_candidate_artifact_id"] == "hero-artifact"
    assert result["candidate"]["seed"] == 1701


@pytest.mark.parametrize("payload, message", [
    ({"seed": 1}, "character_card"),
    ({"character_card": {"character_id": "gtee_dev"}}, "integer seed"),
])
def test_hero_candidate_rejects_incomplete_payload(monkeypatch, payload, message):
    import workers.image_worker as worker

    monkeypatch.setattr(worker, "MOCK_MODE", True)
    with pytest.raises(TypeError, match=message):
        worker.handle_hero_candidate_task(
            UploadClient(), {"id": "h1", "claim_token": "c", "payload": payload})


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
    monkeypatch.setattr(worker, "_run_mock", lambda character_id: RENDER_PNG)
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


def _fake_hero_forge2(monkeypatch, script):
    import sys

    import workers.image_worker as worker

    monkeypatch.setattr(worker, "_build_hero_candidate_command",
                        lambda *args: [sys.executable, "-c", script])
    return worker


def test_blank_hero_candidate_carries_forge2_details(monkeypatch):
    worker = _fake_hero_forge2(monkeypatch, (
        "import sys\n"
        f"print({CORRUPT_LINE!r}, file=sys.stderr)\n"
        "sys.exit(3)\n"))
    with pytest.raises(RuntimeError) as err:
        worker._run_hero_candidate({"character_id": "gtee_dev"}, 7, "full")
    assert str(err.value) == CORRUPT_LINE


def test_hero_candidate_crash_is_not_labelled_corrupt(monkeypatch):
    import subprocess

    worker = _fake_hero_forge2(monkeypatch, "import sys; sys.exit(1)")
    with pytest.raises(subprocess.CalledProcessError):
        worker._run_hero_candidate({"character_id": "gtee_dev"}, 7, "full")


def test_blank_hero_candidate_schedules_comfyui_restart_and_still_fails(monkeypatch):
    import workers.image_worker as worker

    def corrupt(card, seed, profile):
        raise RuntimeError("corrupt_output: ComfyUI returned a blank/uniform image")

    monkeypatch.setattr(worker, "MOCK_MODE", False)
    monkeypatch.setattr(worker.memory, "memory_shortfall", lambda profile: None)
    monkeypatch.setattr(worker, "_run_hero_candidate", corrupt)
    sup = FakeSupervisor()
    with pytest.raises(RuntimeError, match=r"^corrupt_output:"):
        worker.handle_hero_candidate_task(UploadClient(), HERO_TASK, StageRecorder(), sup)
    assert "request_restart" in sup.calls
    assert "restart" not in sup.calls
    assert sup.calls[-1] == "idle"


class RecordingClient:
    def __init__(self):
        self.uploaded = {}

    def upload_artifact(self, content, content_type, filename):
        self.uploaded = {"content": content, "content_type": content_type,
                         "filename": filename}
        return {"id": "artifact-1"}


def _mock_post(monkeypatch, fmt=None):
    import workers.image_worker as worker

    monkeypatch.setattr(worker, "MOCK_MODE", True)
    monkeypatch.setattr(worker, "MOCK_DELAY_SECONDS", 0)
    monkeypatch.setattr(worker, "_run_mock", lambda character_id: RENDER_PNG)
    if fmt is not None:
        monkeypatch.setattr(worker, "POST_IMAGE_FORMAT", fmt)
    client = RecordingClient()
    assert worker.handle_task(client, TASK) == {"image_artifact_id": "artifact-1"}
    return client.uploaded


def test_post_image_defaults_to_webp():
    import workers.image_worker as worker

    assert worker.POST_IMAGE_FORMAT == "webp"


def test_post_render_is_uploaded_as_webp(monkeypatch):
    uploaded = _mock_post(monkeypatch, "webp")

    assert uploaded["content_type"] == "image/webp"
    assert uploaded["filename"] == "t1.webp"
    image = Image.open(io.BytesIO(uploaded["content"]))
    assert image.format == "WEBP" and image.size == (64, 64)


def test_post_render_can_be_uploaded_as_jpeg(monkeypatch):
    uploaded = _mock_post(monkeypatch, "jpeg")

    assert uploaded["content_type"] == "image/jpeg"
    assert uploaded["filename"] == "t1.jpg"
    assert Image.open(io.BytesIO(uploaded["content"])).format == "JPEG"


def test_post_render_png_setting_uploads_render_unchanged(monkeypatch):
    uploaded = _mock_post(monkeypatch, "png")

    assert uploaded == {"content": RENDER_PNG, "content_type": "image/png",
                        "filename": "t1.png"}


def test_converted_post_render_keeps_outlines():
    import workers.image_worker as worker

    content, _, _ = worker._encode_post_image(RENDER_PNG, "webp")
    decoded = Image.open(io.BytesIO(content)).convert("RGB")
    line, background = decoded.getpixel((10, 32)), decoded.getpixel((10, 10))
    # The 1px black line stays clearly dark against the blue (200) background.
    assert max(line) < max(background) / 2


def test_unknown_post_image_format_is_refused():
    import workers.image_worker as worker

    with pytest.raises(ValueError, match="POST_IMAGE_FORMAT"):
        worker._encode_post_image(RENDER_PNG, "gif")


def test_hero_candidate_stays_png_whatever_post_format(monkeypatch):
    import workers.image_worker as worker

    monkeypatch.setattr(worker, "MOCK_MODE", True)
    monkeypatch.setattr(worker, "MOCK_DELAY_SECONDS", 0)
    monkeypatch.setattr(worker, "POST_IMAGE_FORMAT", "webp")
    client = RecordingClient()
    worker.handle_hero_candidate_task(client, HERO_TASK)

    assert client.uploaded["content_type"] == "image/png"
    assert client.uploaded["filename"] == "h1.png"
