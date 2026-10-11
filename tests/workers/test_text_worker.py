from datetime import datetime, timedelta, timezone

import pytest

from workers.text_worker import handle_text_task


class FakeOllama:
    default_model = "qwen3:14b"

    def __init__(self):
        self.payloads = []

    def chat(self, payload):
        self.payloads.append(payload)
        return {"content": "draft", "provider": "ollama", "model": "qwen3:14b", "duration_ms": 5}


class FakeSupervisor:
    def __init__(self, managed):
        self.managed, self.stopped = managed, False

    def stop(self):
        self.stopped = True


class Stages:
    def __init__(self):
        self.stages = []

    def update(self, *, state, task=None, stage=None, message=None):
        self.stages.append(stage)


def _task(deadline_delta=timedelta(minutes=10), **payload):
    deadline = (datetime.now(timezone.utc) + deadline_delta).isoformat()
    body = {"messages": [{"role": "user", "content": "hi"}], "deadline_at": deadline} | payload
    return {"id": "t1", "claim_token": "c", "task_type": "text_generation", "payload": body}


def test_text_task_returns_provider_result_and_reports_stage():
    ollama, stages = FakeOllama(), Stages()
    result = handle_text_task(_task(), stages, ollama, None)
    assert result["content"] == "draft"
    assert ollama.payloads[0]["messages"] == [{"role": "user", "content": "hi"}]
    assert stages.stages == ["generating_text"]


def test_expired_task_fails_without_calling_ollama():
    ollama = FakeOllama()
    with pytest.raises(RuntimeError, match=r"^expired:"):
        handle_text_task(_task(deadline_delta=timedelta(seconds=-1)), None, ollama, None)
    assert ollama.payloads == []


def test_task_without_deadline_still_runs():
    ollama = FakeOllama()
    task = _task()
    del task["payload"]["deadline_at"]
    assert handle_text_task(task, None, ollama, None)["content"] == "draft"


def test_worker_owned_comfyui_is_stopped_before_text_work():
    sup = FakeSupervisor(managed=True)
    handle_text_task(_task(), None, FakeOllama(), sup)
    assert sup.stopped


def test_user_started_comfyui_is_left_alone():
    sup = FakeSupervisor(managed=False)
    handle_text_task(_task(), None, FakeOllama(), sup)
    assert not sup.stopped


def test_empty_messages_are_rejected():
    with pytest.raises(ValueError, match="messages"):
        handle_text_task(_task(messages=[]), None, FakeOllama(), None)
