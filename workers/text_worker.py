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

from workers.deadline import raise_if_expired


def handle_text_task(task: dict, heartbeat, ollama, supervisor) -> dict:
    payload = task["payload"]
    if not payload.get("messages"):
        raise ValueError("text_generation payload needs non-empty messages")
    # The producer has already given up; running it would only burn GPU time.
    raise_if_expired(payload)
    if supervisor is not None and supervisor.managed:
        # Ollama and a loaded ComfyUI can't share the 24 GB Mac; free ours.
        supervisor.stop()
    if heartbeat:
        model = payload.get("model") or ollama.default_model
        heartbeat.update(state="busy", task=task, stage="generating_text",
                         message=f"Generating text with {model}")
    return ollama.chat(payload)
