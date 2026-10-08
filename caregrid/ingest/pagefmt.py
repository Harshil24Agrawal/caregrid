"""Markdown + YAML frontmatter page format shared by compile.py and (later) knowledge/brain.py."""
from __future__ import annotations

from pathlib import Path

import yaml

from caregrid.models import Page, PageType, Precedent


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


def page_relpath(page: Page) -> str:
    """Policies are stored per version (policy/KA-12@v3.md); every other page type has one file per id."""
    if page.type == PageType.POLICY:
        return f"policy/{page.id}@v{page.version}.md"
    return f"{page.type.value}/{page.id}.md"


def page_text(page: Page) -> str:
    return render_page(page.model_dump(mode="json", exclude={"body"}), page.body)


def precedent_relpath(prec: Precedent) -> str:
    return f"precedent/{prec.id}.md"


def precedent_title(prec: Precedent) -> str:
    return f"{prec.request_type}: {prec.decision_code.value}"


def precedent_text(prec: Precedent) -> str:
    fm = {"id": prec.id, "type": "precedent", **prec.model_dump(mode="json", exclude={"id", "summary"})}
    return render_page(fm, prec.summary)


def render_index(entries: list[dict]) -> str:
    """entries: dicts with id, type, status, title, path."""
    lines = ["# Second Brain index", ""]
    for e in sorted(entries, key=lambda e: (e["type"], e["id"], e["path"])):
        lines.append(f"- [{e['id']}]({e['path']}) - {e['type']} - {e['status']} - {e['title']}")
    return "\n".join(lines) + "\n"


def log_line(actor: str, action: str, page_id: str, reason: str, when: str) -> str:
    return f"- {when} | {actor} | {action} | {page_id} | {reason}\n"
