"""Notebook — pure vault-tree listing + wikilink resolution.

No kernel import, no I/O: the app hands in the vault's note paths (from
VaultIndex, which already excludes dot-segments and non-markdown files) and
note text; everything here is unit-testable without a daemon.

Resolution is the SDK's ``resolve_link_target`` over ``build_link_lookup`` —
the same functions the `link` app's index uses — so a link `link` counts as
resolved is one notebook can open, and vice versa.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from emptyos.sdk.markdown_render import strip_code
from emptyos.sdk.utils import (
    WIKILINK_RE,
    build_link_lookup,
    normalize_link_target,
    resolve_link_target,
)

build_lookup = build_link_lookup
resolve = resolve_link_target

# `C:` or `C:/…` — a drive root, never a vault folder. `A: Areas` is a folder.
_DRIVE = re.compile(r"^[A-Za-z]:(/|$)")
# Attachments an `![[…]]` embed points at — media and documents, not notes.
# Same extension set the page renderer embeds (EOS_UI.renderMarkdown).
_ATTACHMENT = re.compile(r"\.(svg|png|jpe?g|gif|webp|avif|bmp|mp4|webm|mov|mp3|wav|ogg|pdf)$", re.I)


def normalize_dir(raw: str) -> tuple[str, str | None]:
    """A caller-supplied folder, as a vault-relative POSIX path, or an error.

    ``""`` is the vault root. This is not a safety boundary — ``list_dir`` only
    filters the in-memory index, so no value reaches the disk — it exists so a
    path that can never name a vault folder gets an honest error instead of an
    empty listing. A colon is legal in a folder name on Linux/macOS vaults, so
    only a leading drive letter is refused.
    """
    s = str(raw or "").strip()
    d = s.replace("\\", "/").strip("/")
    if not d:
        return "", None
    if s.startswith(("/", "\\")) or _DRIVE.match(d):
        return "", f"invalid folder {raw!r} — must be a vault-relative path"
    parts = d.split("/")
    if any(p in ("", ".", "..") or p.startswith(".") for p in parts):
        return "", f"invalid folder {raw!r} — no empty, '.', '..' or hidden segments"
    return "/".join(parts), None


def list_dir(paths: Iterable[str], folder: str) -> dict:
    """The direct children of ``folder``: sub-folders (with note counts) + notes.

    Folders are derived from note paths, so a folder holding no markdown at any
    depth does not appear — the tree is a view of notes, not of the disk.
    """
    prefix = folder + "/" if folder else ""
    folders: dict[str, int] = {}
    files: list[dict] = []
    for p in paths:
        if not p.startswith(prefix):
            continue
        rest = p[len(prefix):]
        if "/" in rest:
            name = rest.split("/", 1)[0]
            folders[name] = folders.get(name, 0) + 1
        else:
            files.append({"name": rest[:-3] if rest.lower().endswith(".md") else rest, "path": p})
    return {
        "dir": folder,
        "folders": [
            {"name": n, "path": prefix + n, "count": folders[n]}
            for n in sorted(folders, key=str.lower)
        ],
        "files": sorted(files, key=lambda f: f["name"].lower()),
    }


def outgoing_links(
    text: str, by_path: dict[str, str], by_stem: dict[str, list[str]], source: str = ""
) -> list[dict]:
    """Each distinct wikilink target in ``text`` with the notes it resolves to.

    Code spans and fences are ignored (a ``[[x]]`` in a sample is not a link),
    embedded attachments (``![[photo.jpg]]``) are not notes, and a note never
    lists itself — `link` drops self-links the same way. Order is first
    appearance, which is how a reader meets them.
    """
    seen: set[str] = set()
    out: list[dict] = []
    body = strip_code(text or "")
    for m in WIKILINK_RE.finditer(body):
        target = m.group(1).strip()
        key = normalize_link_target(target)
        embed = m.start() > 0 and body[m.start() - 1] == "!"
        if not key or key in seen or (embed and _ATTACHMENT.search(key)):
            continue
        seen.add(key)
        paths = [p for p in resolve_link_target(target, by_path, by_stem) if p != source]
        if source and not paths and resolve_link_target(target, by_path, by_stem):
            continue  # the only note it names is this one
        out.append({"target": target, "paths": paths})
    return out
