import pytest

from workers.image_worker import _build_real_command


def test_real_command_forwards_explicit_profile():
    command = _build_real_command("gtee_dev", "turning a dial", 42, "lightning")
    assert command[command.index("--profile") + 1] == "lightning"
    assert command[command.index("--seed") + 1] == "42"


def test_real_command_rejects_unknown_profile():
    with pytest.raises(ValueError, match="unsupported image profile"):
        _build_real_command("gtee_dev", "turning a dial", 42, "turbo")
