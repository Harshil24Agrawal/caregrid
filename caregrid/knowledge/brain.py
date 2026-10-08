"""The Second Brain page store: markdown+frontmatter files loaded into memory (CONTRACTS §3)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from caregrid.ingest.pagefmt import (
    log_line, page_relpath, page_text, precedent_relpath, precedent_text, precedent_title, read_page_file,
    render_index, write_text,
)
from caregrid.models import Page, PageStatus, PageType, Precedent

_TYPE_DIRS = [t.value for t in PageType]


class Brain:
    def __init__(self, brain_dir: Path) -> None:
        self.dir = Path(brain_dir)
        self._pages: dict[str, dict[int, Page]] = {}
        self._prec: dict[str, Precedent] = {}
        self.reload()

    # ------------------------------------------------------------ loading
    def reload(self) -> None:
        """Re-read every page from disk (Streamlit caches the Brain; call this after external changes)."""
        pages: dict[str, dict[int, Page]] = {}
        precs: dict[str, Precedent] = {}
        for sub in _TYPE_DIRS:
            for f in sorted((self.dir / sub).glob("*.md")):
                fm, body = read_page_file(f)
                if fm.get("type") == "precedent":
                    precs[fm["id"]] = Precedent(**{k: v for k, v in fm.items() if k != "type"}, summary=body)
                else:
                    page = Page(**fm, body=body)
                    pages.setdefault(page.id, {})[page.version] = page
        self._pages, self._prec = pages, precs

    # ------------------------------------------------------------ reads
    @staticmethod
    def _is_current_status(status: PageStatus) -> bool:
        return status == PageStatus.APPROVED

    def _current(self, page_id: str) -> Page | None:
        versions = self._pages.get(page_id)
        if not versions:
            return None
        approved = [p for p in versions.values() if self._is_current_status(p.status)]
        return max(approved, key=lambda p: p.version) if approved else None

    def get(self, page_id: str, version: int | None = None) -> Page | None:
        """version=None -> the current APPROVED page (ACTIVE precedent). An explicit version returns that
        page whatever its status, so callers can see that it is draft/expired/stale."""
        if page_id in self._prec:
            prec = self._prec[page_id]
            if version is None and prec.status != PageStatus.ACTIVE:
                return None
            if version not in (None, 1):
                return None
            return self._precedent_view(prec)
        if version is None:
            return self._current(page_id)
        return self._pages.get(page_id, {}).get(version)

    @staticmethod
    def _precedent_view(prec: Precedent) -> Page:
        return Page(
            id=prec.id, type=PageType.PRECEDENT, title=precedent_title(prec), version=1, status=prec.status,
            effective_from=prec.date, request_types=[prec.request_type], body=prec.summary,
            meta={"facts": prec.facts, "decision_code": prec.decision_code.value, "route_team": prec.route_team,
                  "policy_id": prec.policy_id, "policy_version": prec.policy_version, "outcome": prec.outcome},
        )

    def get_precedent(self, precedent_id: str) -> Precedent | None:
        """Any precedent regardless of status."""
        return self._prec.get(precedent_id)

    def all_pages(self) -> list[Page]:
        """Every stored version of every non-precedent page."""
        return [p for versions in self._pages.values() for _, p in sorted(versions.items())]

    def current_pages(self, *types: PageType) -> list[Page]:
        out = [p for pid in sorted(self._pages) if (p := self._current(pid)) is not None]
        return [p for p in out if not types or p.type in types]

    def current_policies(self) -> list[Page]:
        """Approved current policy pages only (no draft, no expired)."""
        return self.current_pages(PageType.POLICY)

    def workflow_for(self, request_type: str) -> Page | None:
        for p in self.current_pages(PageType.WORKFLOW):
            if request_type in p.request_types:
                return p
        return None

    def team(self, team_id: str) -> Page | None:
        p = self._current(team_id)
        return p if p is not None and p.type == PageType.TEAM else None

    def precedents(self, request_type: str | None = None, status: PageStatus | None = None) -> list[Precedent]:
        return [p for _, p in sorted(self._prec.items())
                if (request_type is None or p.request_type == request_type) and (status is None or p.status == status)]

    # ------------------------------------------------------------ writes
    def write_precedent(self, p: Precedent) -> None:
        write_text(self.dir / precedent_relpath(p), precedent_text(p))
        self._prec[p.id] = p
        self.append_log("system", "write_precedent", p.id, f"{p.decision_code.value} for {p.request_type}")
        self.rebuild_index()

    def write_page(self, page: Page, publish: bool = False) -> None:
        """Publish `page` as the next version of page.id: the previous current version becomes EXPIRED, precedents
        that cite an older policy version become STALE, the change is logged and the index rebuilt.

        A DRAFT page is refused (ValueError) unless `publish=True`, which only the Knowledge-PR approval path passes."""
        if page.status == PageStatus.DRAFT:
            if not publish:
                self.append_log("system", "write_page_refused", page.id, "draft pages cannot be written without PR approval")
                raise ValueError(f"refusing to write DRAFT page {page.id}: it must go through Knowledge-PR approval (publish=True)")
            self.append_log("system", "publish_draft", page.id, "draft published through PR approval")
        versions = self._pages.get(page.id, {})
        new_version = max(versions, default=0) + 1
        previous = self._current(page.id)
        new = page.model_copy(update={"version": new_version, "status": PageStatus.APPROVED})
        if previous is not None:
            expired = previous.model_copy(update={"status": PageStatus.EXPIRED})
            versions[previous.version] = expired
            if page.type == PageType.POLICY:  # versions live side by side; other types are replaced in place
                write_text(self.dir / page_relpath(expired), page_text(expired))
        versions[new_version] = new
        self._pages[page.id] = versions
        write_text(self.dir / page_relpath(new), page_text(new))
        self.append_log("system", "write_page", new.id, f"published v{new_version}")
        stale = self.mark_stale_for_policy(new.id, new_version) if new.type == PageType.POLICY else []
        if not stale:
            self.rebuild_index()

    def retire_page(self, page_id: str) -> list[str]:
        """Retire the current version WITHOUT publishing a successor: it becomes EXPIRED, every active precedent that cites the policy
        becomes STALE (whatever version it cited), the change is logged and the index rebuilt. Returns the ids that went stale."""
        current = self._current(page_id)
        if current is None:
            raise KeyError(f"{page_id} has no approved current version to retire")
        expired = current.model_copy(update={"status": PageStatus.EXPIRED})
        self._pages[page_id][current.version] = expired
        write_text(self.dir / page_relpath(expired), page_text(expired))
        self.append_log("system", "retire_page", page_id, f"v{current.version} retired")
        stale = self.mark_stale_for_policy(page_id, -1) if current.type == PageType.POLICY else []
        if not stale:
            self.rebuild_index()
        return stale

    def mark_stale_for_policy(self, policy_id: str, current_version: int) -> list[str]:
        """Active precedents that cite `policy_id` at a version other than `current_version` become STALE."""
        changed: list[str] = []
        for pid, prec in sorted(self._prec.items()):
            if prec.policy_id == policy_id and prec.status == PageStatus.ACTIVE and prec.policy_version != current_version:
                prec = prec.model_copy(update={"status": PageStatus.STALE})
                self._prec[pid] = prec
                write_text(self.dir / precedent_relpath(prec), precedent_text(prec))
                changed.append(pid)
        if changed:
            self.append_log("system", "mark_stale", policy_id, f"{len(changed)} precedent(s) stale: {', '.join(changed)}")
            self.rebuild_index()
        return changed

    def append_log(self, actor: str, action: str, page_id: str, reason: str) -> None:
        path = self.dir / "log.md"
        stamp = datetime.now().isoformat(timespec="seconds")
        existing = path.read_text(encoding="utf-8") if path.exists() else "# Second Brain log\n\n"
        write_text(path, existing + log_line(actor, action, page_id, reason, stamp))

    def rebuild_index(self) -> None:
        entries = [{"id": p.id, "type": p.type.value, "status": p.status.value, "title": p.title, "path": page_relpath(p)}
                   for p in self.all_pages()]
        entries += [{"id": r.id, "type": "precedent", "status": r.status.value, "title": precedent_title(r),
                     "path": precedent_relpath(r)} for r in self._prec.values()]
        write_text(self.dir / "index.md", render_index(entries))
