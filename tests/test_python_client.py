"""The client never sends a producer credential to a worker endpoint or vice versa."""
from clients.python_client import TaskQueueClient


class FakeResponse:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {"id": "result"}


def test_producer_methods_send_only_the_producer_key(monkeypatch):
    calls = []

    def record(method):
        def request(url, **kwargs):
            calls.append((method, url, kwargs))
            return FakeResponse()
        return request

    monkeypatch.setattr("clients.python_client.httpx.post", record("post"))
    monkeypatch.setattr("clients.python_client.httpx.get", record("get"))
    client = TaskQueueClient("https://example.test", producer_api_key="producer-secret",
                             worker_api_key="worker-secret")

    client.submit_task("test", {})
    client.get_task("task-1")
    client.get_artifact("artifact-1")

    assert len(calls) == 3
    assert all(call[2]["headers"] == {"X-API-Key": "producer-secret"} for call in calls)


def test_worker_methods_send_only_the_worker_key(monkeypatch):
    calls = []

    def record(method):
        def request(url, **kwargs):
            calls.append((method, url, kwargs))
            return FakeResponse()
        return request

    monkeypatch.setattr("clients.python_client.httpx.post", record("post"))
    monkeypatch.setattr("clients.python_client.httpx.get", record("get"))
    client = TaskQueueClient("https://example.test", producer_api_key="producer-secret",
                             worker_api_key="worker-secret")

    client.register_worker("worker-1", "Worker", "session-1", {})
    client.heartbeat("worker-1", "session-1", "idle")
    client.claim_next(["test"], "worker-1")
    client.complete_task("task-1", "claim-1", {})
    client.fail_task("task-1", "claim-1", "error")
    client.upload_artifact(b"content", "text/plain")

    assert len(calls) == 6
    assert all(call[2]["headers"] == {"X-API-Key": "worker-secret"} for call in calls)


def test_unconfigured_role_omits_the_header(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append(kwargs)
        return FakeResponse()

    monkeypatch.setattr("clients.python_client.httpx.post", fake_post)
    TaskQueueClient("https://example.test").submit_task("test", {})
    assert calls[0]["headers"] == {}


def test_canonical_suite_page_maps_to_product_scoped_api(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return FakeResponse()

    monkeypatch.setattr("clients.python_client.httpx.get", fake_get)
    client = TaskQueueClient("https://ai-bharatiya-web.vercel.app/queue")
    client.get_task("task-1")
    assert calls == ["https://ai-bharatiya-web.vercel.app/api/queue/tasks/task-1"]


def test_explicit_product_api_base_is_not_modified():
    client = TaskQueueClient("https://example.test/api/queue/")
    assert client.api_url == "https://example.test/api/queue"
