import pytest

from workers import memory

GIB = 1024 ** 3


def test_mac_available_memory_includes_reclaimable_pages():
    vm_stat = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                               100.
Pages active:                             900.
Pages inactive:                           200.
Pages speculative:                         20.
"""
    assert memory._mac_available_bytes(vm_stat) == 320 * 16384


def test_linux_available_memory_uses_memavailable():
    assert memory._linux_available_bytes("MemAvailable: 1024 kB\n") == 1024 * 1024


def test_helper_processes_are_grouped_under_their_app():
    assert memory._application_name("/usr/local/bin/ollama runner") == "Ollama"
    assert memory._application_name(
        "/Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Helper"
    ) == "Google Chrome"


def test_required_gb_defaults(monkeypatch):
    monkeypatch.delenv("MEMORY_REQUIRED_FULL_GB", raising=False)
    monkeypatch.delenv("MEMORY_REQUIRED_LIGHTNING_GB", raising=False)
    assert memory.required_gb("full") == 14.0
    assert memory.required_gb("lightning") == 0.0


def test_required_gb_is_configurable(monkeypatch):
    monkeypatch.setenv("MEMORY_REQUIRED_FULL_GB", "16")
    assert memory.required_gb("full") == 16.0


def test_full_render_short_on_memory_reports_insufficient(monkeypatch):
    monkeypatch.setattr(memory, "available_memory_bytes", lambda: int(9.5 * GIB))
    monkeypatch.setattr(memory, "top_memory_consumers",
                        lambda limit=3: [{"name": "Ollama", "memory_gb": 9.1}])
    message = memory.memory_shortfall("full")
    assert message.startswith("insufficient_memory:")
    assert "9.5 GB" in message and "14" in message and "Ollama" in message


def test_enough_memory_has_no_shortfall(monkeypatch):
    monkeypatch.setattr(memory, "available_memory_bytes", lambda: 20 * GIB)
    assert memory.memory_shortfall("full") is None


def test_lightning_is_not_gated_by_default(monkeypatch):
    monkeypatch.delenv("MEMORY_REQUIRED_LIGHTNING_GB", raising=False)
    monkeypatch.setattr(memory, "available_memory_bytes", lambda: 1 * GIB)
    assert memory.memory_shortfall("lightning") is None


def test_unmeasurable_memory_does_not_block(monkeypatch):
    def boom():
        raise RuntimeError("vm_stat missing")

    monkeypatch.setattr(memory, "available_memory_bytes", boom)
    assert memory.memory_shortfall("full") is None


def test_memory_details_shape(monkeypatch):
    monkeypatch.setattr(memory, "available_memory_bytes", lambda: int(18.25 * GIB))
    monkeypatch.setattr(memory, "top_memory_consumers", lambda limit=3: [])
    details = memory.memory_details()
    assert details["available_gb"] == 18.25
    assert details["required_gb"]["full"] == memory.required_gb("full")
    assert details["top_consumers"] == []


def test_memory_details_survives_measurement_failure(monkeypatch):
    monkeypatch.setattr(memory, "available_memory_bytes",
                        lambda: (_ for _ in ()).throw(RuntimeError("nope")))
    assert memory.memory_details() is None


@pytest.fixture(autouse=True)
def _no_env_leak(monkeypatch):
    monkeypatch.delenv("MEMORY_REQUIRED_FULL_GB", raising=False)
