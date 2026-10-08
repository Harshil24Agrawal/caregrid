"""Markdown + YAML frontmatter page format shared by compile.py and (later) knowledge/brain.py."""
from __future__ import annotations

from pathlib import Path

import yaml


def render_page(frontmatter: dict, body: str) -> str:
    fm = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True, default_flow_style=False, width=1000)
    return f"---\n{fm}---\n\n{body.strip()}\n"


def split_frontmatter(text: str) -> tuple[dict, str]:
    text = text.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return {}, text
    end = text.index("\n---\n", 4)
    return yaml.safe_load(text[4:end]) or {}, text[end + 5 :].strip()


def read_page_file(path: Path) -> tuple[dict, str]:
    return split_frontmatter(path.read_text(encoding="utf-8"))


def write_text(path: Path, text: str) -> None:
    """UTF-8, LF line endings on every platform (byte-identical regeneration)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
