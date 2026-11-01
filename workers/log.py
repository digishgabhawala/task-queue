from datetime import datetime


def log(message: str) -> None:
    print(
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {message}",  # noqa: DTZ005
        flush=True,
    )
