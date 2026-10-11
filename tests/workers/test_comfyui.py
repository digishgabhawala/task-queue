import subprocess

import pytest

from workers.comfyui import ComfyUISupervisor, ComfyUIUnavailable


class FakeProc:
    def __init__(self):
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


class Harness:
    """Fake process launcher, health probe and clock for the supervisor."""

    def __init__(self, healthy=False, healthy_after_polls=None):
        self.healthy = healthy
        self.healthy_after_polls = healthy_after_polls
        self.polls = 0
        self.now = 0.0
        self.launched = []

    def health(self, url):
        self.polls += 1
        if self.healthy_after_polls is not None and self.polls >= self.healthy_after_polls:
            self.healthy = True
        return self.healthy

    def popen(self, cmd, **kwargs):
        proc = FakeProc()
        self.launched.append((cmd, kwargs, proc))
        return proc

    def sleep(self, seconds):
        self.now += seconds

    def clock(self):
        return self.now

    def supervisor(self, tmp_path, **overrides):
        options = {"url": "http://127.0.0.1:8188", "start_cmd": ["start-comfy"],
                   "log_path": tmp_path / "comfyui.log", "idle_seconds": 600,
                   "start_timeout": 30, "health": self.health, "popen": self.popen,
                   "clock": self.clock, "sleep": self.sleep}
        return ComfyUISupervisor(**(options | overrides))


def test_user_started_comfyui_is_used_but_never_managed(tmp_path):
    h = Harness(healthy=True)
    sup = h.supervisor(tmp_path)
    sup.ensure_running()
    assert h.launched == []
    assert sup.details() == {"state": "ready", "managed": False}
    sup.mark_idle()
    h.now = 10_000
    assert sup.maybe_stop_idle() is False
    assert sup.restart() is False


def test_starts_comfyui_in_its_own_session_and_waits_for_health(tmp_path):
    h = Harness(healthy=False, healthy_after_polls=3)
    sup = h.supervisor(tmp_path)
    sup.ensure_running()
    cmd, kwargs, _ = h.launched[0]
    assert cmd == ["start-comfy"]
    assert kwargs["start_new_session"] is True
    assert sup.details() == {"state": "ready", "managed": True}


def test_start_timeout_cleans_up_child_and_raises(tmp_path):
    h = Harness(healthy=False)
    sup = h.supervisor(tmp_path)
    with pytest.raises(ComfyUIUnavailable, match=r"^comfyui_unavailable:"):
        sup.ensure_running()
    assert h.launched[0][2].terminated
    assert sup.details()["state"] == "failed"


def test_child_exiting_during_start_raises_without_waiting_out_timeout(tmp_path):
    h = Harness(healthy=False)

    def popen(cmd, **kwargs):
        proc = FakeProc()
        proc.returncode = 1
        h.launched.append((cmd, kwargs, proc))
        return proc

    sup = h.supervisor(tmp_path, popen=popen, start_timeout=10_000)
    with pytest.raises(ComfyUIUnavailable, match="exited"):
        sup.ensure_running()
    assert h.now < 10_000


def test_management_off_and_not_running_raises_without_launching(tmp_path):
    h = Harness(healthy=False)
    sup = h.supervisor(tmp_path, manage=False)
    with pytest.raises(ComfyUIUnavailable, match="not running"):
        sup.ensure_running()
    assert h.launched == []


def test_idle_managed_comfyui_is_stopped_after_timeout(tmp_path):
    h = Harness(healthy=False, healthy_after_polls=2)
    sup = h.supervisor(tmp_path)
    sup.ensure_running()
    sup.mark_idle()
    h.now += 599
    assert sup.maybe_stop_idle() is False
    h.now += 1
    assert sup.maybe_stop_idle() is True
    assert h.launched[0][2].terminated
    h.healthy, h.healthy_after_polls = False, None  # the server is really gone now
    assert sup.details() == {"state": "stopped", "managed": False}


def test_busy_comfyui_is_never_idle_stopped(tmp_path):
    h = Harness(healthy=False, healthy_after_polls=2)
    sup = h.supervisor(tmp_path)
    sup.ensure_running()
    sup.mark_busy()
    h.now += 100_000
    assert sup.maybe_stop_idle() is False


def test_restart_replaces_managed_process(tmp_path):
    h = Harness(healthy=False, healthy_after_polls=2)
    sup = h.supervisor(tmp_path)
    sup.ensure_running()
    assert sup.restart() is True
    assert h.launched[0][2].terminated
    assert len(h.launched) == 2
    assert sup.details() == {"state": "ready", "managed": True}


def test_crashed_managed_comfyui_is_restarted_on_next_task(tmp_path):
    h = Harness(healthy=False, healthy_after_polls=2)
    sup = h.supervisor(tmp_path)
    sup.ensure_running()
    h.launched[0][2].returncode = 1  # crashed
    # probes: details() -> down, ensure_running() -> down, then healthy after launch
    h.healthy, h.healthy_after_polls = False, h.polls + 3
    assert sup.details()["state"] == "stopped"
    sup.ensure_running()
    assert len(h.launched) == 2


def test_stop_escalates_to_kill_when_terminate_hangs(tmp_path):
    h = Harness(healthy=False, healthy_after_polls=2)
    sup = h.supervisor(tmp_path)
    sup.ensure_running()
    proc = h.launched[0][2]

    def stubborn_terminate():
        proc.terminated = True

    def wait(timeout=None):
        if proc.returncode is None:
            raise subprocess.TimeoutExpired("comfy", timeout)
        return proc.returncode

    proc.terminate, proc.wait = stubborn_terminate, wait
    sup.stop()
    assert proc.killed


def test_details_reports_user_started_comfyui_before_any_task(tmp_path):
    h = Harness(healthy=True)
    sup = h.supervisor(tmp_path)
    assert sup.details() == {"state": "ready", "managed": False}
    assert h.launched == []


def test_details_reports_stopped_when_nothing_is_running(tmp_path):
    h = Harness(healthy=False)
    assert h.supervisor(tmp_path).details() == {"state": "stopped", "managed": False}
