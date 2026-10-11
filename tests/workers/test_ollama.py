import httpx
import pytest

from workers.ollama import OllamaManager


class Resp:
    def __init__(self, status=200, data=None):
        self.status_code, self._data = status, data or {}

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=self)


class FakeOllama:
    """Stands in for the Ollama HTTP API (/api/ps, /api/chat, /api/generate, /api/tags)."""

    def __init__(self, loaded=(), down=False, chat_status=200):
        self.loaded = set(loaded)
        self.down = down
        self.chat_status = chat_status
        self.posts = []

    def get(self, url, timeout):
        if self.down:
            raise httpx.ConnectError("refused")
        if url.endswith("/api/ps"):
            return Resp(data={"models": [{"name": m, "model": m} for m in self.loaded]})
        return Resp(data={"models": []})  # /api/tags

    def post(self, url, json, timeout):
        if self.down:
            raise httpx.ConnectError("refused")
        self.posts.append((url, json))
        if url.endswith("/api/chat"):
            if self.chat_status != 200:
                return Resp(self.chat_status, {"error": "model not found"})
            self.loaded.add(json["model"])
            return Resp(data={"model": json["model"], "message": {"content": "hello"}})
        if url.endswith("/api/generate") and json.get("keep_alive") == 0:
            self.loaded.discard(json["model"])
        return Resp()


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def manager(fake, clock=None, idle=300):
    return OllamaManager("http://ollama", "qwen3:14b", idle_seconds=idle, http=fake,
                         clock=clock or Clock())


PAYLOAD = {"messages": [{"role": "user", "content": "hi"}], "temperature": 0.2,
           "reasoning": True, "response_format": "json"}


def test_chat_translates_contract_to_ollama():
    fake = FakeOllama()
    result = manager(fake).chat(PAYLOAD)
    url, body = fake.posts[0]
    assert url == "http://ollama/api/chat"
    assert body == {"model": "qwen3:14b", "messages": PAYLOAD["messages"], "stream": False,
                    "think": True, "options": {"temperature": 0.2}, "format": "json"}
    assert result["content"] == "hello"
    assert result["provider"] == "ollama" and result["model"] == "qwen3:14b"
    assert isinstance(result["duration_ms"], int)


def test_text_format_and_model_override():
    fake = FakeOllama()
    manager(fake).chat({"messages": [{"role": "user", "content": "x"}],
                        "response_format": "text", "model": "llama3:8b"})
    body = fake.posts[0][1]
    assert "format" not in body
    assert body["model"] == "llama3:8b"
    assert body["think"] is False and body["options"] == {"temperature": 0.7}


def test_model_loaded_by_worker_is_owned_and_unloaded_after_idle():
    fake, clock = FakeOllama(), Clock()
    m = manager(fake, clock)
    m.chat(PAYLOAD)
    assert m.details() == {"model": "qwen3:14b", "loaded": True, "managed": True}
    clock.now = 299
    assert m.maybe_unload_idle() is False
    clock.now = 300
    assert m.maybe_unload_idle() is True
    assert "qwen3:14b" not in fake.loaded
    assert m.details()["managed"] is False


def test_foreign_loaded_model_is_never_unloaded():
    fake, clock = FakeOllama(loaded=["qwen3:14b"]), Clock()
    m = manager(fake, clock)
    m.chat(PAYLOAD)
    assert m.details()["managed"] is False
    clock.now = 10_000
    assert m.maybe_unload_idle() is False
    m.unload()
    assert "qwen3:14b" in fake.loaded
    assert not any(url.endswith("/api/generate") for url, _ in fake.posts)


def test_unreachable_ollama_raises_text_unavailable():
    with pytest.raises(RuntimeError, match=r"^text_unavailable:"):
        manager(FakeOllama(down=True)).chat(PAYLOAD)


def test_missing_model_raises_text_unavailable():
    with pytest.raises(RuntimeError, match=r"^text_unavailable:"):
        manager(FakeOllama(chat_status=404)).chat(PAYLOAD)


def test_details_and_availability_when_ollama_is_down():
    m = manager(FakeOllama(down=True))
    assert m.available() is False
    assert m.details() == {"model": "qwen3:14b", "loaded": False, "managed": False}
    assert manager(FakeOllama()).available() is True
