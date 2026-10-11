"""Fast, dependency-free checks for the static admin UI foundation."""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI_DIR = ROOT / "app" / "admin_ui"
HTML_PATH = UI_DIR / "index.html"
TOKENS_PATH = UI_DIR / "tokens.css"
CSS_PATH = UI_DIR / "admin.css"


class AdminHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.references: list[tuple[str, str]] = []
        self.local_assets: list[str] = []
        self.html_language: str | None = None
        self.has_viewport = False
        self.buttons_without_type = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "html":
            self.html_language = values.get("lang")
        if tag == "meta" and values.get("name") == "viewport":
            self.has_viewport = bool(values.get("content"))
        if tag == "button" and not values.get("type"):
            self.buttons_without_type += 1

        element_id = values.get("id")
        if element_id:
            self.ids.append(element_id)

        for attribute in ("for", "aria-controls", "aria-describedby", "aria-labelledby"):
            for reference in (values.get(attribute) or "").split():
                self.references.append((attribute, reference))

        for attribute in ("href", "src"):
            value = values.get(attribute) or ""
            if value.startswith("/admin-assets/"):
                self.local_assets.append(value)


def fail(messages: list[str]) -> None:
    for message in messages:
        print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    html = HTML_PATH.read_text(encoding="utf-8")
    tokens = TOKENS_PATH.read_text(encoding="utf-8")
    css = CSS_PATH.read_text(encoding="utf-8")
    errors: list[str] = []

    definitions = set(re.findall(r"(--[a-z0-9-]+)\s*:", tokens + "\n" + css))
    references = set(re.findall(r"var\(\s*(--[a-z0-9-]+)", css))
    missing_tokens = sorted(references - definitions)
    if missing_tokens:
        errors.append(f"undefined CSS custom properties: {', '.join(missing_tokens)}")

    raw_hex = sorted(set(re.findall(r"(?<![\w-])#[0-9a-fA-F]{3,8}\b", css)))
    if raw_hex:
        errors.append(f"admin.css bypasses shared tokens with raw colors: {', '.join(raw_hex)}")

    parser = AdminHTMLParser()
    parser.feed(html)
    duplicate_ids = sorted({value for value in parser.ids if parser.ids.count(value) > 1})
    if duplicate_ids:
        errors.append(f"duplicate HTML IDs: {', '.join(duplicate_ids)}")

    missing_references = sorted(
        f'{attribute}="{reference}"'
        for attribute, reference in parser.references
        if reference not in parser.ids
    )
    if missing_references:
        errors.append(f"references to missing HTML IDs: {', '.join(missing_references)}")

    if not parser.html_language:
        errors.append("the document must declare an HTML language")
    if not parser.has_viewport:
        errors.append("the document must include a viewport meta tag")
    if parser.buttons_without_type:
        errors.append(f"{parser.buttons_without_type} button(s) do not declare a type")

    for asset in parser.local_assets:
        asset_path = UI_DIR / asset.removeprefix("/admin-assets/")
        if not asset_path.is_file():
            errors.append(f"missing local asset referenced by HTML: {asset}")

    token_link = html.find('href="/admin-assets/tokens.css"')
    component_link = html.find('href="/admin-assets/admin.css"')
    if token_link < 0 or component_link < 0 or token_link > component_link:
        errors.append("tokens.css must be loaded before admin.css")

    if errors:
        fail(errors)

    print(
        "PASS: admin UI foundation "
        f"({len(definitions)} tokens, {len(parser.ids)} unique IDs, "
        f"{len(parser.local_assets)} local assets)"
    )


if __name__ == "__main__":
    main()
