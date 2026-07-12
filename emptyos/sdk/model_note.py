"""emptyos/sdk/model_note — one domain model stored verbatim in one vault note.

The "study note" storage pattern shared by engineering-study apps (``power-study``,
``reliability``): a single domain model (a plain dict) is the source of truth and
is stored in one markdown note as a fenced ```json block, with a handful of
queryable summary fields in the frontmatter. No per-element notes, no
frontmatter→model assembler — the JSON fence *is* the model.

Pure functions — no kernel, no I/O. The owning app keeps its own
``vault_read`` / ``vault_write`` / ``vault_dir.glob`` and supplies its tag, intro
line, and per-app frontmatter summary fields. This module owns only the
(error-prone, previously duplicated) serialize/parse skeleton.

Distinct from ``emptyos/sdk/vault_model.py`` — that wraps *typed frontmatter*
(Pydantic). This is the *opaque-JSON-body* pattern: the frontmatter carries only
display/summary fields; the real model lives in the fence.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import TYPE_CHECKING

from emptyos.sdk.utils import slugify

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

JSON_FENCE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)


def slugify_id(s: str) -> str:
    """Filesystem-safe study id: lowercase, non-alphanumerics → '-'. Empty → 'study'."""
    return slugify(s, max_len=None, fallback="study")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def parse_model_note(raw: str) -> dict | None:
    """Extract the model dict from a note's ```json fence; None if absent/invalid."""
    m = JSON_FENCE.search(raw or "")
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except (json.JSONDecodeError, ValueError):
        return None


def serialize_model_note(
    model: dict,
    *,
    tag: str,
    intro: str = "",
    extra_frontmatter: dict | None = None,
    now: str | None = None,
) -> str:
    """Render a model dict into a vault note: frontmatter + intro + ```json fence.

    Frontmatter is ``id`` / ``title`` / ``tags:[tag]`` / ``created`` / ``updated`` /
    ``author``, followed by ``extra_frontmatter`` (the app's queryable summary
    fields, kept in insertion order). ``created`` is preserved from the model when
    present, else set to ``now``; ``updated`` is always ``now``.
    """
    now = now or now_iso()
    title = model.get("title") or model["id"]
    lines = [
        "---",
        f"id: {model['id']}",
        f"title: {title}",
        "tags:",
        f"  - {tag}",
        f"created: {model.get('created') or now}",
        f"updated: {now}",
        f"author: {model.get('author') or 'user'}",
    ]
    for k, v in (extra_frontmatter or {}).items():
        lines.append(f"{k}: {v}")
    lines += ["---", ""]
    fm = "\n".join(lines)
    body = (
        f"# {title}\n\n"
        + (intro.rstrip() + "\n\n" if intro else "")
        + "## Model\n\n```json\n"
        + json.dumps(model, indent=2, ensure_ascii=False)
        + "\n```\n"
    )
    return fm + body


class ModelNoteStore:
    """Mixin: iterate the model-note files in a study app's ``vault_dir``.

    The "study note" *list* scaffold — glob ``vault_dir/*.md`` → ``parse_model_note``
    → skip the unreadable/empty ones — was copied verbatim into every engineering
    study app (``power-study``, ``reliability``, ``overhead-line``, ``design-package``).
    This mixin owns that one error-prone loop so each app's ``_list_*`` keeps only
    its per-row summary.

    Compose onto a ``BaseApp`` subclass — it relies on ``self.vault_dir`` alone.
    The read/write/filename helpers stay per-app on purpose: they are one-liners
    over the already-shared ``slugify_id`` / ``parse_model_note`` /
    ``serialize_model_note``, and each app names them in its own domain
    (``_read_study`` / ``_read_design`` / ``_read_pkg``). Don't fold those in —
    consolidating them buys nothing and forces a rename across every call site.
    """

    def iter_model_notes(self) -> "Iterator[tuple[Path, dict]]":
        """Yield ``(file, model)`` for every parseable model note in ``vault_dir``.

        Sorted by filename. A file that raises ``OSError`` on read, or whose body
        has no valid ```json fence, is skipped silently — the same tolerance every
        ``_list_*`` had inline. The owning app builds its summary row per yield.
        """
        d = self.vault_dir  # type: ignore[attr-defined]  # provided by BaseApp
        if not d.exists():
            return
        for f in sorted(d.glob("*.md")):
            try:
                model = parse_model_note(f.read_text(encoding="utf-8"))
            except OSError:
                continue
            if model:
                yield f, model
