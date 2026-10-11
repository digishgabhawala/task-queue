"""`text_generation` task handler for the local worker.

Task payload contract (provider-neutral; see README):
    {"messages": [{"role": "system"|"user"|"assistant", "content": str}],
     "temperature": float, "reasoning": bool, "response_format": "text"|"json",
     "model": str | None, "deadline_at": ISO-8601 UTC}
Result:
    {"content": str, "provider": str, "model": str, "duration_ms": int}
Failure prefixes: "expired:" (producer stopped waiting -- never run),
"text_unavailable:" (provider down / model missing).
"""
from __future__ import annotations

from datetime import datetime, timezone


def _deadline_passed(deadline_at: str | None) -> bool:
    if not deadline_at:
        return False
    deadline = datetime.fromisoformat(deadline_at.replace("Z", "+00:00"))
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) > deadline


def handle_text_task(task: dict, heartbeat, ollama, supervisor) -> dict:
    payload = task["payload"]
    if not payload.get("messages"):
        raise ValueError("text_generation payload needs non-empty messages")
    if _deadline_passed(payload.get("deadline_at")):
        # The producer has already given up; running it would only burn
        # GPU time (possibly an hour late, behind a render).
        raise RuntimeError(
            f"expired: deadline {payload['deadline_at']} passed before a worker was free")
    if supervisor is not None and supervisor.managed:
        # Ollama and a loaded ComfyUI can't share the 24 GB Mac; free ours.
        supervisor.stop()
    if heartbeat:
        model = payload.get("model") or ollama.default_model
        heartbeat.update(state="busy", task=task, stage="generating_text",
                         message=f"Generating text with {model}")
    return ollama.chat(payload)
