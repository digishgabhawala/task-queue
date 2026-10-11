"""Free-memory checks the image worker runs on its own machine.

Ported from linkedin-content-agent's former resource_service.py (repos share
no code). The check lives here because the worker may run anywhere -- only
the machine that renders knows whether it has room to. Full-quality renders
need about 14 GB free (calibrated on the 24 GB Mac, where ComfyUI's ~20B edit
model competing with Ollama produced silent black images). Lightning is not
gated by default: it's the user's fallback choice when Full is refused.
"""
from __future__ import annotations

import os
import platform
import re
import subprocess
from collections import defaultdict
from pathlib import Path

GIB = 1024 ** 3
_DEFAULT_REQUIRED_GB = {"full": 14.0, "lightning": 0.0}


def _run(command: list[str]) -> str:
    return subprocess.run(
        command, check=True, capture_output=True, text=True, timeout=10
    ).stdout


def _mac_available_bytes(vm_stat: str) -> int:
    page_match = re.search(r"page size of (\d+) bytes", vm_stat)
    if not page_match:
        raise RuntimeError("vm_stat did not report its page size")
    page_size = int(page_match.group(1))
    pages: dict[str, int] = {}
    for line in vm_stat.splitlines():
        match = re.match(r"([^:]+):\s+(\d+)\.?$", line.strip())
        if match:
            pages[match.group(1)] = int(match.group(2))
    # Free plus reclaimable inactive/speculative pages -- the "available"
    # definition common macOS system monitors use.
    available_pages = sum(
        pages.get(name, 0)
        for name in ("Pages free", "Pages inactive", "Pages speculative")
    )
    return available_pages * page_size


def _linux_available_bytes(meminfo: str) -> int:
    match = re.search(r"^MemAvailable:\s+(\d+)\s+kB$", meminfo, re.MULTILINE)
    if not match:
        raise RuntimeError("/proc/meminfo did not report MemAvailable")
    return int(match.group(1)) * 1024


def available_memory_bytes() -> int:
    system = platform.system()
    if system == "Darwin":
        return _mac_available_bytes(_run(["vm_stat"]))
    if system == "Linux":
        return _linux_available_bytes(Path("/proc/meminfo").read_text())
    page_size = int(_run(["getconf", "PAGE_SIZE"]).strip())
    available_pages = int(_run(["getconf", "_AVPHYS_PAGES"]).strip())
    return page_size * available_pages


def _application_name(command: str) -> str:
    lower = command.lower()
    known = (
        ("google chrome", "Google Chrome"),
        ("visual studio code", "Visual Studio Code"),
        ("code helper", "Visual Studio Code"),
        ("com.docker", "Docker"),
        ("docker desktop", "Docker"),
        ("virtualization.virtualmachine", "Docker"),
        ("ollama", "Ollama"),
        ("comfyui", "ComfyUI"),
        ("safari", "Safari"),
        ("slack", "Slack"),
    )
    for needle, name in known:
        if needle in lower:
            return name
    app_match = re.search(r"/([^/]+)\.app/Contents/", command, re.IGNORECASE)
    if app_match:
        return app_match.group(1)
    executable = command.strip().split(maxsplit=1)[0] if command.strip() else "Unknown"
    return Path(executable).name or "Unknown"


def top_memory_consumers(limit: int = 3) -> list[dict]:
    totals_kib: dict[str, int] = defaultdict(int)
    try:
        output = _run(["ps", "-axo", "rss=,command="])
    except (OSError, subprocess.SubprocessError):
        return []  # sandboxed hosts (e.g. Colab) may deny the process list
    for line in output.splitlines():
        match = re.match(r"\s*(\d+)\s+(.+)$", line)
        if match:
            totals_kib[_application_name(match.group(2))] += int(match.group(1))
    ranked = sorted(totals_kib.items(), key=lambda item: item[1], reverse=True)
    return [{"name": name, "memory_gb": round(kib * 1024 / GIB, 2)}
            for name, kib in ranked[:limit]]


def required_gb(profile: str) -> float:
    env = os.environ.get(f"MEMORY_REQUIRED_{profile.upper()}_GB")
    return float(env) if env else _DEFAULT_REQUIRED_GB.get(profile, 0.0)


def memory_shortfall(profile: str) -> str | None:
    """An `insufficient_memory:` failure message, or None if the render may run.

    A machine whose memory can't be measured is never blocked -- the gate is
    a guard against a known failure mode, not a hard requirement.
    """
    needed = required_gb(profile)
    if needed <= 0:
        return None
    try:
        available = available_memory_bytes() / GIB
    except Exception as exc:
        print(f"memory check skipped: {exc}")
        return None
    if available >= needed:
        return None
    hogs = ", ".join(f"{c['name']} {c['memory_gb']} GB" for c in top_memory_consumers())
    return (f"insufficient_memory: {profile} quality needs {needed:g} GB free, this "
            f"worker has {available:.1f} GB. Free memory or retry with Lightning."
            + (f" Largest apps: {hogs}." if hogs else ""))


def memory_details() -> dict | None:
    """Heartbeat `details.memory` payload; None when it can't be measured."""
    try:
        available = available_memory_bytes()
    except Exception:
        return None
    return {
        "available_gb": round(available / GIB, 2),
        "required_gb": {p: required_gb(p) for p in ("full", "lightning")},
        "top_consumers": top_memory_consumers(),
    }
