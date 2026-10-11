"""Ollama text provider for the local worker, plus model-memory ownership.

Translates the provider-neutral `text_generation` contract to Ollama's
/api/chat. Same ownership rule as ComfyUI: a model this worker caused to load
is "managed" -- unloaded after OLLAMA_IDLE_MINUTES without text work and
before any image render (the two cannot share a 24 GB Mac). A model someone
else already had loaded is used but never unloaded.

`http` (anything with httpx-style get/post) and `clock` are injectable so
behaviour is unit tested without Ollama.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable

import httpx


def _norm(name: str) -> str:
    """Ollama lists an untagged model as `<name>:latest`."""
    return name if ":" in name else f"{name}:latest"


class OllamaManager:
    def __init__(self, url: str, default_model: str, *, idle_seconds: float = 300,
                 timeout: float = 900, http=httpx,
                 clock: Callable[[], float] = time.monotonic):
        self.url = url.rstrip("/")
        self.default_model = default_model
        self.idle_seconds = idle_seconds
        self.timeout = timeout
        self._http = http
        self._clock = clock
        self._owned: str | None = None   # model this worker loaded
        self._last_used = clock()
        self._lock = threading.Lock()

    def _loaded_models(self) -> set[str] | None:
        """Normalised names of loaded models, or None if that can't be known."""
        try:
            resp = self._http.get(f"{self.url}/api/ps", timeout=5)
            resp.raise_for_status()
            models = resp.json().get("models", [])
        except (httpx.HTTPError, ValueError, AttributeError):
            return None
        return {_norm(n) for m in models for n in (m.get("name"), m.get("model")) if n}

    def available(self) -> bool:
        try:
            self._http.get(f"{self.url}/api/tags", timeout=5).raise_for_status()
            return True
        except httpx.HTTPError:
            return False

    def chat(self, payload: dict) -> dict:
        model = payload.get("model") or self.default_model
        body = {
            "model": model,
            "messages": payload["messages"],
            "stream": False,
            "think": bool(payload.get("reasoning", False)),
            "options": {"temperature": payload.get("temperature", 0.7)},
        }
        if payload.get("response_format") == "json":
            body["format"] = "json"

        with self._lock:
            loaded_before = self._loaded_models()
            # Ours only if Ollama positively showed the model NOT loaded. If
            # that can't be known, assume it's the user's: leaving a model
            # loaded is safer than unloading someone else's.
            ours = loaded_before is not None and _norm(model) not in loaded_before
            started = time.monotonic()
            try:
                resp = self._http.post(f"{self.url}/api/chat", json=body, timeout=self.timeout)
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPStatusError as e:
                raise RuntimeError(
                    f"text_unavailable: Ollama rejected model {model!r} "
                    f"(HTTP {e.response.status_code})") from e
            except httpx.HTTPError as e:
                raise RuntimeError(
                    f"text_unavailable: Ollama not reachable at {self.url}: {e}") from e
            finally:
                # Even a failed/timed-out call may have loaded the model --
                # it must still be unloaded before the next render.
                if ours:
                    self._owned = model
                self._last_used = self._clock()
        return {
            "content": (data.get("message") or {}).get("content", ""),
            "provider": "ollama",
            "model": data.get("model", model),
            "duration_ms": int((time.monotonic() - started) * 1000),
        }

    def unload(self) -> None:
        """Free the model's memory -- only if this worker loaded it."""
        with self._lock:
            model, self._owned = self._owned, None
            if model is None:
                return
            try:
                self._http.post(f"{self.url}/api/generate",
                                json={"model": model, "keep_alive": 0}, timeout=30)
                print(f"unloaded Ollama model {model}")
            except httpx.HTTPError as e:
                print(f"could not unload Ollama model {model}: {e}")

    def maybe_unload_idle(self) -> bool:
        if self._owned is None or self._clock() - self._last_used < self.idle_seconds:
            return False
        print(f"Ollama idle for {self.idle_seconds:g}s -- unloading {self._owned}")
        self.unload()
        return True

    def details(self) -> dict:
        loaded = self._loaded_models() or set()
        return {"model": self.default_model, "loaded": _norm(self.default_model) in loaded,
                "managed": self._owned is not None}
