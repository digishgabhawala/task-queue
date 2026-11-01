import re

from workers.log import log


def test_log_prefixes_message_with_local_timestamp(capsys):
    log("hello")

    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} hello",
        capsys.readouterr().out.rstrip("\n"),
    )
