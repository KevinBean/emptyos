"""Publish — posts/ draft↔published folder mirroring.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns the "flag stays the source of truth, folder mirrors it"
convention for a site's ``posts/`` tree: a post carrying ``publish: true``
lives in ``posts/published/``; a draft lives in ``posts/drafts/``. The
``publish:`` frontmatter flag remains authoritative — scan() reads the flag,
never the folder — so a misfiled note never silently breaks or leaks the
site; the folder is a mirror for human tidiness (drag-nothing; the toggle
moves the file). Sidecars (``<stem>.teaser.md`` / ``<stem>.linkedin.md``)
follow their parent so relative image + ``parent_post`` references survive.

Public URLs are slug-based (SiteBuilder.scan → slugify), NOT folder-derived,
so moving a note between ``drafts/`` and ``published/`` never changes a live
URL — the mirror is safe to run on any build.

Cross-module callers reach these via ``self.X`` after re-binding. Consumed by
writer.py (``api_toggle_publish``) and scheduling.py (``_release_due_for_site``)
through ``self._mirror_post_location``; by writer.py (``save_draft``) through
``self._new_post_dir``; and by app.py (``api_build``) through
``self.normalize_post_folders``.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import web_route

if TYPE_CHECKING:
    from .app import PublishApp  # noqa: F401


# ─── Bind to PublishApp class as ─────────────────────────────────────
#   _split_drafts_enabled = _folders._split_drafts_enabled
#   _post_root            = _folders._post_root
#   _post_target_dir      = _folders._post_target_dir
#   _new_post_dir         = _folders._new_post_dir
#   _mirror_post_location = _folders._mirror_post_location
#   normalize_post_folders = _folders.normalize_post_folders
#   api_normalize_folders = _folders.api_normalize_folders
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────

# Subfolder names under <source>/posts/. Kept as constants (not config) —
# the split is a fixed convention; a site opts out via `split_drafts: false`.
_DRAFTS_DIR = "drafts"
_PUBLISHED_DIR = "published"


def _split_drafts_enabled(self, site: dict | None = None) -> bool:
    """Whether this site mirrors posts into drafts/ + published/ subfolders.

    On by default — the folder split is the standing convention. A site sets
    ``split_drafts: false`` in its config to keep posts flat (no auto-move).
    """
    s = site or self._active_site()
    val = s.get("split_drafts", True)
    return bool(val) if val is not None else True


def _post_root(self, site: dict | None = None) -> Path:
    """<vault>/<source>/posts — the tree the split applies to."""
    return Path(self._vault_dir()) / self._source_folder(site) / "posts"


def _post_target_dir(self, published: bool, site: dict | None = None) -> Path:
    """Where a post with the given publish state should live."""
    return self._post_root(site) / (_PUBLISHED_DIR if published else _DRAFTS_DIR)


def _new_post_dir(self, site: dict | None = None) -> Path:
    """Folder a freshly-created draft should be written to.

    posts/drafts/ when the split is on, else the posts/ root (legacy flat).
    """
    if self._split_drafts_enabled(site):
        return self._post_target_dir(False, site)
    return self._post_root(site)


def _post_siblings(main: Path) -> list[Path]:
    """The main .md plus its stem-sharing sidecars (<stem>.teaser.md, etc.).

    Matches ``<stem>.*`` in the same dir so ``foo.md`` picks up
    ``foo.teaser.md`` / ``foo.linkedin.md`` but never ``foo-bar.md``.
    """
    stem = main.name[:-3] if main.name.endswith(".md") else main.stem
    out = []
    for f in sorted(main.parent.glob(f"{stem}.*")):
        if f.is_file() and f.suffix == ".md":
            out.append(f)
    if main not in out and main.is_file():
        out.insert(0, main)
    return out


def _mirror_post_location(
    self, path, published: bool, site: dict | None = None
) -> Path:
    """Move a post (and its sidecars) into the folder matching its state.

    Returns the new path of the main file (or the original if unchanged /
    not applicable). Fail-soft: any move error leaves the flag flip intact
    and returns the original path. Only touches notes already inside the
    site's posts/ tree — pages and root files are never moved.
    """
    main = Path(path)
    if not self._split_drafts_enabled(site):
        return main
    if not main.is_file():
        return main

    post_root = self._post_root(site).resolve()
    try:
        main_res = main.resolve()
        main_res.relative_to(post_root)
    except ValueError:
        # Not under posts/ (a page, a root note) — leave it alone.
        return main

    target_dir = self._post_target_dir(published, site).resolve()
    if main_res.parent == target_dir:
        return main  # already correct

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        self.log_warn(f"posts folder mirror: mkdir failed for {target_dir}: {exc}")
        return main

    new_main = main
    for f in _post_siblings(main):
        dest = target_dir / f.name
        try:
            if dest.exists() and dest.resolve() != f.resolve():
                self.log_warn(
                    f"posts folder mirror: {dest.name} already exists in "
                    f"{target_dir.name}/ — leaving {f} in place"
                )
                continue
            f.replace(dest)
            if f.resolve() == main_res:
                new_main = dest
        except OSError as exc:
            self.log_warn(f"posts folder mirror: move failed for {f}: {exc}")
            if f.resolve() == main_res:
                return main
    return new_main


def normalize_post_folders(self, site: dict | None = None) -> dict:
    """Ensure every post sits in the folder matching its publish flag.

    Idempotent + fail-soft. Returns {moved, published, drafts, skipped}.
    The self-healing counterpart to the manual toggle move — called at the
    top of api_build so the folders can't drift out of sync with the flags.
    """
    s = site or self._active_site()
    if not self._split_drafts_enabled(s):
        return {"moved": 0, "published": 0, "drafts": 0, "skipped": 0}

    moved = published = drafts = skipped = 0
    try:
        items = self.scan(s, include_drafts=True)
    except Exception as exc:
        self.log_warn(f"normalize_post_folders scan failed: {exc}")
        return {"moved": 0, "published": 0, "drafts": 0, "skipped": 0, "error": str(exc)}

    for item in items:
        if item.get("type") != "post":
            continue
        path = item.get("path", "")
        is_pub = not item.get("draft")
        before = Path(path)
        after = self._mirror_post_location(before, is_pub, s)
        if after.resolve() != before.resolve():
            moved += 1
            if is_pub:
                published += 1
            else:
                drafts += 1
        else:
            skipped += 1
    return {"moved": moved, "published": published, "drafts": drafts, "skipped": skipped}


@web_route("POST", "/api/normalize-folders")
async def api_normalize_folders(self, request):
    """Move every post into its drafts/ or published/ folder to match its flag.

    Body: {"site_id": "..."} optional; defaults to the active site. Safe to
    re-run (idempotent) and URL-preserving (slugs are folder-independent).
    """
    try:
        site = await self._site_from_request(request)
    except ValueError as e:
        return {"error": str(e)}
    return self.normalize_post_folders(site)
