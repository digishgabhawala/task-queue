import pytest

from workers import local_worker as lw

TEXT, IMAGE = "text_generation", "image_generation"


def test_task_types_come_from_worker_configuration():
    assert lw.configured_task_types("image_generation, text_generation") == [IMAGE, TEXT]
    assert lw.configured_task_types("text_generation") == [TEXT]


def test_missing_configuration_is_an_error_not_a_guess():
    with pytest.raises(ValueError, match="WORKER_TASK_TYPES"):
        lw.configured_task_types("  ")


def test_unknown_task_type_is_rejected():
    with pytest.raises(ValueError, match="video_generation"):
        lw.configured_task_types("video_generation")


class Queue:
    def __init__(self, task):
        self.task, self.asked = task, []

    def claim_next(self, task_types, worker_id):
        self.asked.append(task_types)
        return self.task


def test_worker_asks_for_the_next_task_of_any_configured_type_in_one_claim():
    # No priority between types: the queue hands out the oldest waiting task.
    q = Queue({"id": "oldest"})
    attempt = lw.claim_next_task(q, "w", [IMAGE, TEXT])
    assert attempt.task["id"] == "oldest" and attempt.reachable is True
    assert q.asked == [[IMAGE, TEXT]]


def test_text_only_worker_never_asks_for_images():
    q = Queue(None)
    attempt = lw.claim_next_task(q, "w", [TEXT])
    assert attempt.task is None and attempt.reachable is True
    assert q.asked == [[TEXT]]


def test_image_worker_entrypoint_defaults_to_images_only(monkeypatch):
    import workers.image_worker as iw

    started = []
    monkeypatch.delenv("WORKER_TASK_TYPES", raising=False)
    monkeypatch.setattr(lw, "main", lambda: started.append(lw.os.environ["WORKER_TASK_TYPES"]))
    iw.main()
    assert started == [IMAGE]


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


def test_network_error_while_claiming_does_not_crash_the_worker():
    import httpx

    class FlakyQueue:
        def claim_next(self, task_types, worker_id):
            raise httpx.ReadTimeout("The read operation timed out")

    attempt = lw.claim_next_task(FlakyQueue(), "w", [TEXT])
    assert attempt.task is None and attempt.reachable is False  # logged; next poll retries


def test_polling_backs_off_only_when_queue_confirms_it_is_empty(monkeypatch):
    monkeypatch.setattr(lw.image_worker, "POLL_SECONDS", 3)
    monkeypatch.setattr(lw.image_worker, "IDLE_POLL_SECONDS", 60)

    assert lw.next_poll_delay(lw.ClaimAttempt(None, reachable=True)) == 60
    assert lw.next_poll_delay(lw.ClaimAttempt({"id": "task-1"}, reachable=True)) == 3
    assert lw.next_poll_delay(lw.ClaimAttempt(None, reachable=False)) == 3


def test_network_error_while_reporting_a_result_does_not_crash_the_worker():
    import httpx

    class FlakyQueue:
        def complete_task(self, task_id, claim_token, result):
            raise httpx.ConnectError("connection reset")

        def fail_task(self, task_id, claim_token, error):
            raise httpx.ReadTimeout("timed out")

    task = {"id": "t1", "claim_token": "c", "task_type": TEXT}
    lw.report_result(FlakyQueue(), task, {"content": "x"})        # must not raise
    lw.report_failure(FlakyQueue(), task, RuntimeError("boom"))   # must not raise


class UnloadingOllama:
    def __init__(self, unloaded):
        self.unloaded = unloaded

    def unload(self):
        return self.unloaded


def test_image_after_unloading_own_model_waits_for_memory_to_be_released(monkeypatch):
    answers = iter(["insufficient_memory: 1.1 GB", "insufficient_memory: 6 GB", None])
    slept = []
    monkeypatch.setattr(lw.memory, "memory_shortfall", lambda profile: next(answers))
    monkeypatch.setattr(lw.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(lw.image_worker, "handle_task", lambda c, t, h, s: {"img": 1})
    task = {"id": "i", "task_type": IMAGE, "payload": {"profile": "full"}}
    assert lw.run_task(None, task, None, None, UnloadingOllama(True)) == {"img": 1}
    assert len(slept) == 2  # waited until the freed memory showed up


def test_no_wait_when_nothing_was_unloaded(monkeypatch):
    monkeypatch.setattr(lw.memory, "memory_shortfall",
                        lambda profile: pytest.fail("no unload, no settle wait"))
    monkeypatch.setattr(lw.image_worker, "handle_task", lambda c, t, h, s: {"img": 1})
    task = {"id": "i", "task_type": IMAGE, "payload": {"profile": "full"}}
    assert lw.run_task(None, task, None, None, UnloadingOllama(False)) == {"img": 1}


def test_settle_wait_is_bounded(monkeypatch):
    monkeypatch.setattr(lw.memory, "memory_shortfall", lambda profile: "insufficient_memory: x")
    clock = iter(range(0, 1000, 5))
    monkeypatch.setattr(lw.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(lw.time, "sleep", lambda s: None)
    lw.wait_for_memory_release("full", timeout=60)  # returns; the real gate then decides
