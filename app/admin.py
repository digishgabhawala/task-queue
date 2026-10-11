from pathlib import Path

from fastapi.responses import HTMLResponse

_ADMIN_HTML = Path(__file__).with_name("admin_ui") / "index.html"


def admin_page() -> HTMLResponse:
    return HTMLResponse(_ADMIN_HTML.read_text(encoding="utf-8"))
