import pytest

from workers import local_worker as lw

TEXT, IMAGE = "text_generation", "image_generation"


def test_explicit_task_types_win_over_detection():
    assert lw.detect_task_types("text_generation", lambda: False, lambda: True) == [TEXT]


def test_auto_detection_uses_what_this_machine_can_do():
    assert lw.detect_task_types("", lambda: True, lambda: True) == [TEXT, IMAGE]
    assert lw.detect_task_types("", lambda: True, lambda: False) == [TEXT]
    assert lw.detect_task_types("", lambda: False, lambda: True) == [IMAGE]


def test_unknown_task_type_is_rejected():
    with pytest.raises(ValueError, match="video_generation"):
        lw.detect_task_types("video_generation", lambda: True, lambda: True)


def test_claim_order_puts_text_first():
    assert lw.claim_order([IMAGE, TEXT]) == [TEXT, IMAGE]


class Queue:
    def __init__(self, tasks_by_type):
        self.tasks, self.asked = tasks_by_type, []

    def claim_next(self, task_types, worker_id):
        self.asked.append(task_types)
        return self.tasks.get(task_types[0])


def test_waiting_text_task_is_claimed_before_waiting_image():
    q = Queue({TEXT: {"id": "t"}, IMAGE: {"id": "i"}})
    assert lw.claim_next_task(q, "w", [TEXT, IMAGE])["id"] == "t"
    assert q.asked == [[TEXT]]


def test_image_claimed_when_no_text_waiting():
    q = Queue({IMAGE: {"id": "i"}})
    assert lw.claim_next_task(q, "w", [TEXT, IMAGE])["id"] == "i"


def test_text_only_worker_never_asks_for_images():
    q = Queue({IMAGE: {"id": "i"}})
    assert lw.claim_next_task(q, "w", [TEXT]) is None
    assert q.asked == [[TEXT]]


class FakeOllama:
    def __init__(self):
        self.unloaded = 0

    def unload(self):
        self.unloaded += 1


def test_text_task_dispatches_to_text_handler(monkeypatch):
    seen = []
    monkeypatch.setattr(lw, "handle_text_task",
                        lambda task, hb, ollama, sup: seen.append(task["id"]) or {"content": "x"})
    result = lw.run_task(None, {"id": "t", "task_type": TEXT}, None, None, FakeOllama())
    assert result == {"content": "x"} and seen == ["t"]


def test_image_task_unloads_worker_model_first(monkeypatch):
    ollama, order = FakeOllama(), []
    monkeypatch.setattr(lw.image_worker, "handle_task",
                        lambda client, task, hb, sup: order.append(ollama.unloaded) or {"img": 1})
    assert lw.run_task(None, {"id": "i", "task_type": IMAGE}, None, None, ollama) == {"img": 1}
    assert order == [1]  # unloaded before rendering


def test_unsupported_task_type_fails():
    with pytest.raises(ValueError, match="unsupported"):
        lw.run_task(None, {"id": "x", "task_type": "video_generation"}, None, None, None)


def test_heartbeat_registers_detected_capabilities():
    from workers.image_worker import WorkerHeartbeat

    registered = {}

    class Client:
        def register_worker(self, *args, capabilities, version):
            registered.update(capabilities)

    WorkerHeartbeat(Client(), capabilities={"task_types": [TEXT]}).register()
    assert registered == {"task_types": [TEXT]}
