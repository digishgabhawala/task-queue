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


class GarbledResp(Resp):
    def json(self):
        raise ValueError("not json")


class FakeOllama:
    """Stands in for the Ollama HTTP API (/api/ps, /api/chat, /api/generate, /api/tags)."""

    def __init__(self, loaded=(), down=False, chat_status=200, ps_status=200,
                 ps_garbled=False):
        self.loaded = set(loaded)
        self.down = down
        self.chat_status = chat_status
        self.ps_status = ps_status
        self.ps_garbled = ps_garbled
        self.posts = []

    def get(self, url, timeout):
        if self.down:
            raise httpx.ConnectError("refused")
        if url.endswith("/api/ps"):
            if self.ps_garbled:
                return GarbledResp()
            return Resp(self.ps_status,
                        {"models": [{"name": m, "model": m} for m in self.loaded]})
        return Resp(data={"models": []})  # /api/tags

    def post(self, url, json, timeout):
        if self.down:
            raise httpx.ConnectError("refused")
        self.posts.append((url, json))
        if url.endswith("/api/chat"):
            if self.chat_status != 200:
                if self.chat_status >= 500:
                    self.loaded.add(json["model"])  # loaded, then failed mid-generation
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


def test_model_loaded_by_a_failed_chat_is_still_ours_to_unload():
    fake = FakeOllama(chat_status=500)
    m = manager(fake)
    with pytest.raises(RuntimeError, match=r"^text_unavailable:"):
        m.chat(PAYLOAD)
    m.unload()  # e.g. before the next render
    assert "qwen3:14b" not in fake.loaded


def test_unknown_loaded_state_never_claims_ownership():
    fake = FakeOllama(loaded=["qwen3:14b"], ps_status=500)  # ps broken, model is the user's
    m = manager(fake)
    m.chat(PAYLOAD)
    assert m.details()["managed"] is False
    m.unload()
    assert "qwen3:14b" in fake.loaded


def test_garbled_ps_reply_is_treated_as_unknown():
    fake = FakeOllama(ps_garbled=True)
    m = manager(fake)
    assert m.chat(PAYLOAD)["content"] == "hello"
    assert m.details()["managed"] is False


def test_untagged_model_name_matches_latest_tag():
    fake = FakeOllama(loaded=["qwen3:latest"])
    m = OllamaManager("http://ollama", "qwen3", http=fake, clock=Clock())
    m.chat({"messages": [{"role": "user", "content": "x"}]})
    assert m.details() == {"model": "qwen3", "loaded": True, "managed": False}


def test_unload_reports_whether_it_freed_anything():
    fake, clock = FakeOllama(), Clock()
    m = manager(fake, clock)
    assert m.unload() is False          # nothing of ours loaded
    m.chat(PAYLOAD)
    assert m.unload() is True           # unloaded our own model
