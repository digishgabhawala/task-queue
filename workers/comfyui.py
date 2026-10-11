"""ComfyUI process supervision for the image worker.

The worker is started by hand; ComfyUI is not. This starts ComfyUI when a
task needs it, stops it after an idle period (its loaded models are what
starve Ollama and other apps of unified memory), and restarts it after a
blank render or crash -- long ComfyUI uptimes were one of the two root causes
of silent black images (see character-forge-v2 README, Known issues).

Ownership rule: a ComfyUI the worker found already running (started by the
user) is used as-is and is NEVER stopped or restarted -- only a process this
supervisor launched itself is "managed".

Process, health probe, clock and sleep are injectable so behaviour is unit
tested without a real ComfyUI.
"""
from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

import httpx


class ComfyUIUnavailable(RuntimeError):
    """ComfyUI could not be reached or started; message starts with comfyui_unavailable:."""


def _http_health(url: str) -> bool:
    try:
        return httpx.get(f"{url.rstrip('/')}/system_stats", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


class ComfyUISupervisor:
    def __init__(self, url: str, start_cmd: list[str], log_path: Path, *,
                 manage: bool = True, idle_seconds: float = 600,
                 start_timeout: float = 300,
                 health: Callable[[str], bool] = _http_health,
                 popen: Callable[..., subprocess.Popen] = subprocess.Popen,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.url = url
        self.start_cmd = start_cmd
        self.log_path = Path(log_path)
        self.manage = manage
        self.idle_seconds = idle_seconds
        self.start_timeout = start_timeout
        self._health = health
        self._popen = popen
        self._clock = clock
        self._sleep = sleep
        self._proc: subprocess.Popen | None = None
        self._state = "stopped"
        self._busy = False
        self._last_used = clock()
        # The heartbeat thread reads details() while the main loop mutates.
        self._lock = threading.RLock()

    @property
    def managed(self) -> bool:
        return self._proc is not None

    def _child_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def ensure_running(self) -> None:
        with self._lock:
            if self._proc is not None and not self._child_alive():
                self._proc = None  # our ComfyUI crashed; start a fresh one below
            if self._health(self.url):
                self._state = "ready"
                return
            if self._proc is not None:
                # Ours, alive but unresponsive (hung): replace it.
                self._terminate()
            if not self.manage:
                self._state = "failed"
                raise ComfyUIUnavailable(
                    f"comfyui_unavailable: ComfyUI is not running at {self.url} "
                    "and COMFYUI_MANAGE is off -- start it manually.")
            self._start()

    def _start(self) -> None:
        self._state = "starting"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "ab") as log:
            self._proc = self._popen(self.start_cmd, stdout=log,
                                     stderr=subprocess.STDOUT, start_new_session=True)
        deadline = self._clock() + self.start_timeout
        while self._clock() < deadline:
            if not self._child_alive():
                code = self._proc.poll()
                self._proc = None
                self._state = "failed"
                raise ComfyUIUnavailable(
                    f"comfyui_unavailable: ComfyUI exited during startup (code {code}); "
                    f"see {self.log_path}")
            if self._health(self.url):
                self._state = "ready"
                self._last_used = self._clock()
                return
            self._sleep(2)
        self._terminate()
        self._state = "failed"
        raise ComfyUIUnavailable(
            f"comfyui_unavailable: ComfyUI did not respond at {self.url} within "
            f"{self.start_timeout:g}s; see {self.log_path}")

    def _terminate(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=20)

    def stop(self) -> None:
        """Stop a managed ComfyUI; a user-started one is left alone."""
        with self._lock:
            if self._proc is None:
                return
            self._terminate()
            self._state = "stopped"

    def restart(self) -> bool:
        """Restart a managed ComfyUI. Returns False (and does nothing) if unmanaged."""
        with self._lock:
            if self._proc is None:
                return False
            self._state = "restarting"
            self._terminate()
            self._start()
            return True

    def mark_busy(self) -> None:
        with self._lock:
            self._busy = True

    def mark_idle(self) -> None:
        with self._lock:
            self._busy = False
            self._last_used = self._clock()

    def maybe_stop_idle(self) -> bool:
        with self._lock:
            if (self._proc is None or self._busy
                    or self._clock() - self._last_used < self.idle_seconds):
                return False
            print(f"ComfyUI idle for {self.idle_seconds:g}s -- stopping it to free memory")
            self.stop()
            return True

    def details(self) -> dict:
        """Heartbeat `details.comfyui`. Notices a crash between tasks."""
        with self._lock:
            if self._state == "ready" and not self._busy and not self._health(self.url):
                if self._proc is not None and not self._child_alive():
                    self._proc = None
                self._state = "stopped"
            return {"state": self._state, "managed": self.managed}
