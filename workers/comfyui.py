"""ComfyUI process supervision for the image worker.

The worker is started by hand; ComfyUI is not. This starts ComfyUI when a
task needs it, stops it after an idle period (its loaded models are what
starve Ollama and other apps of unified memory), and restarts it after a
blank render or crash -- long ComfyUI uptimes were one of the two root causes
of silent black images (see character-forge-v2 README, Known issues).

Ownership rule: a ComfyUI the worker found already running (started by the
user) is used as-is and is NEVER stopped or restarted -- only a process this
supervisor launched is "managed". The launched PID is recorded in a pidfile,
so a worker that died without cleanup (closed terminal, crash) can be
replaced by one that adopts its orphan instead of mistaking it for the
user's.

Process, health probe, clock, sleep and adoption are injectable so behaviour
is unit tested without a real ComfyUI.
"""
from __future__ import annotations

import os
import signal
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


class _AdoptedProcess:
    """Popen-like handle for a ComfyUI started by a previous worker process."""

    def __init__(self, pid: int):
        self.pid = pid
        self.returncode: int | None = None

    def poll(self) -> int | None:
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            self.returncode = 0 if self.returncode is None else self.returncode
            return self.returncode
        except PermissionError:
            pass
        return None

    def _signal(self, sig: int) -> None:
        try:
            os.kill(self.pid, sig)
        except ProcessLookupError:
            pass

    def terminate(self) -> None:
        self._signal(signal.SIGTERM)

    def kill(self) -> None:
        self._signal(signal.SIGKILL)

    def wait(self, timeout: float | None = None) -> int:
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.poll() is None:
            if deadline is not None and time.monotonic() > deadline:
                raise subprocess.TimeoutExpired(str(self.pid), timeout)
            time.sleep(0.2)
        return self.returncode


def _adopt_comfyui(pid: int) -> _AdoptedProcess | None:
    """Return a handle if `pid` is still a running ComfyUI (main.py), else None."""
    try:
        command = subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                                 capture_output=True, text=True, timeout=5, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return _AdoptedProcess(pid) if "main.py" in command else None


class ComfyUISupervisor:
    def __init__(self, url: str, start_cmd: list[str], log_path: Path, *,
                 manage: bool = True, idle_seconds: float = 600,
                 start_timeout: float = 300, pid_path: Path | None = None,
                 health: Callable[[str], bool] = _http_health,
                 popen: Callable[..., subprocess.Popen] = subprocess.Popen,
                 adopt: Callable[[int], object | None] = _adopt_comfyui,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.url = url
        self.start_cmd = start_cmd
        self.log_path = Path(log_path)
        self.pid_path = Path(pid_path) if pid_path else self.log_path.with_suffix(".pid")
        self.manage = manage
        self.idle_seconds = idle_seconds
        self.start_timeout = start_timeout
        self._health = health
        self._popen = popen
        self._adopt = adopt
        self._clock = clock
        self._sleep = sleep
        self._proc = None
        self._state = "stopped"
        self._busy = False
        self._restart_pending = False
        self._last_used = clock()
        # Serialises start/stop/restart. The heartbeat thread never waits on
        # it (see details()): a cold start can take minutes, and a heartbeat
        # stalled that long would make the worker look offline and expire
        # its task lease.
        self._lock = threading.RLock()

    @property
    def managed(self) -> bool:
        return self._proc is not None

    def _child_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _adopt_orphan(self) -> None:
        """Take ownership of a ComfyUI a previous worker started but never stopped."""
        if self._proc is not None or not self.pid_path.exists():
            return
        try:
            pid = int(self.pid_path.read_text().strip())
        except (OSError, ValueError):
            pid = None
        proc = self._adopt(pid) if pid else None
        if proc is None:
            self.pid_path.unlink(missing_ok=True)  # stale
            return
        print(f"adopted ComfyUI pid {pid} left by a previous worker")
        self._proc = proc

    def ensure_running(self) -> None:
        with self._lock:
            if self._proc is not None and not self._child_alive():
                self._proc = None  # our ComfyUI crashed; start a fresh one below
            healthy = self._health(self.url)
            if healthy:
                self._adopt_orphan()
            if self._restart_pending and self._proc is not None:
                # Scheduled after a blank render; done now, before the next
                # task, so the failure itself was reported without delay.
                self._restart_pending = False
                self._state = "restarting"
                self._terminate()
                self._start()
                return
            self._restart_pending = False
            if healthy:
                self._state = "ready"
                return
            if self._proc is not None:
                self._terminate()  # ours, alive but unresponsive (hung): replace it
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
        try:
            self.pid_path.write_text(str(self._proc.pid))
        except OSError as exc:
            print(f"could not record ComfyUI pid: {exc}")
        deadline = self._clock() + self.start_timeout
        while self._clock() < deadline:
            if not self._child_alive():
                code = self._proc.poll()
                self._proc = None
                self.pid_path.unlink(missing_ok=True)
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
        self.pid_path.unlink(missing_ok=True)
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=20)
        except (subprocess.TimeoutExpired, OSError) as exc:
            # Also runs from atexit: log, never raise during shutdown.
            print(f"ComfyUI pid {getattr(proc, 'pid', '?')} did not exit cleanly: {exc}")

    def stop(self) -> None:
        """Stop a managed ComfyUI; a user-started one is left alone."""
        with self._lock:
            if self._proc is None:
                return
            self._terminate()
            self._state = "stopped"

    def restart(self) -> bool:
        """Restart a managed ComfyUI now. Returns False (and does nothing) if unmanaged."""
        with self._lock:
            if self._proc is None:
                return False
            self._state = "restarting"
            self._terminate()
            self._start()
            return True

    def request_restart(self) -> None:
        """Restart a managed ComfyUI before the next task (no-op if unmanaged)."""
        with self._lock:
            self._restart_pending = True

    def mark_busy(self) -> None:
        with self._lock:
            self._busy = True

    def mark_idle(self) -> None:
        with self._lock:
            self._busy = False
            self._last_used = self._clock()

    def maybe_stop_idle(self) -> bool:
        with self._lock:
            self._adopt_orphan()
            if (self._proc is None or self._busy
                    or self._clock() - self._last_used < self.idle_seconds):
                return False
            print(f"ComfyUI idle for {self.idle_seconds:g}s -- stopping it to free memory")
            self.stop()
            return True

    def details(self) -> dict:
        """Heartbeat `details.comfyui`. Never blocks on a start/stop in progress."""
        if not self._lock.acquire(blocking=False):
            return {"state": self._state, "managed": self.managed}
        try:
            if self._busy:
                # No HTTP probe mid-render: a loading 20B model can make
                # ComfyUI slow to answer (false "stopped"). Our own child's
                # exit status is a local, reliable signal instead.
                if self._proc is not None and not self._child_alive():
                    self._proc = None
                    self.pid_path.unlink(missing_ok=True)
                    self._state = "stopped"
            elif self._state in ("ready", "stopped", "failed"):
                healthy = self._health(self.url)
                if healthy:
                    self._adopt_orphan()
                    self._state = "ready"  # incl. a ComfyUI started by hand
                elif self._state == "ready":
                    if self._proc is not None and not self._child_alive():
                        self._proc = None
                        self.pid_path.unlink(missing_ok=True)
                    self._state = "stopped"
            return {"state": self._state, "managed": self.managed}
        finally:
            self._lock.release()
