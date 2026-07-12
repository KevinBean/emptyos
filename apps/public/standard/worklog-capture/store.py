"""worklog-capture queue store — dir-per-entry, pure file I/O.

Each pending capture is a directory ``<queue>/<id>/`` holding ``shot.png`` (the
image, absent for image-less captures), ``meta.json`` (the record below), and
``ocr.md`` (extracted text, absent when no OCR backend ran). Machine telemetry
under ``data/`` — never the vault (CLAUDE.md storage rule); the vault is only
touched on Apply, through ``worklog.log_work``.

No ``self``, no kernel — imported by both ``app.py`` and ``digest.py`` so neither
depends on the other. ``meta.json`` shape::

    {id, status, source, created, note,
     app_name, window_title, browser_url, browser_title, selection, has_image,
     group_leader, members, evidence, draft, applied, error}

status: new -> drafted -> applied | dismissed   (+ merged for grouped members, error)
"""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime
from pathlib import Path

STATUS_NEW = "new"
STATUS_DRAFTED = "drafted"
STATUS_APPLIED = "applied"
STATUS_DISMISSED = "dismissed"
STATUS_MERGED = "merged"
STATUS_ERROR = "error"

# Terminal states swept after retention_days.
_SWEEPABLE = {STATUS_APPLIED, STATUS_DISMISSED, STATUS_MERGED}


def gen_id(now: datetime | None = None, rand: str | None = None) -> str:
    now = now or datetime.now()
    rand = rand or secrets.token_hex(2)
    return f"cap-{now:%Y%m%d-%H%M%S}-{rand}"


def entry_dir(queue: Path, cid: str) -> Path:
    return Path(queue) / cid


def meta_path(queue: Path, cid: str) -> Path:
    return entry_dir(queue, cid) / "meta.json"


def shot_path(queue: Path, cid: str) -> Path:
    return entry_dir(queue, cid) / "shot.png"


def ocr_path(queue: Path, cid: str) -> Path:
    return entry_dir(queue, cid) / "ocr.md"


def new_meta(cid: str, source: str, created: str, **fields) -> dict:
    meta = {
        "id": cid,
        "status": STATUS_NEW,
        "source": source,
        "created": created,
        "note": "",
        "app_name": "",
        "window_title": "",
        "browser_url": "",
        "browser_title": "",
        "selection": "",
        "has_image": False,
        "group_leader": None,
        "members": [],
        "evidence": [],
        "draft": None,
        "applied": None,
        "error": "",
    }
    meta.update({k: v for k, v in fields.items() if v is not None})
    return meta


def write_meta(queue: Path, cid: str, meta: dict) -> None:
    p = meta_path(queue, cid)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)  # atomic


def read_meta(queue: Path, cid: str) -> dict | None:
    p = meta_path(queue, cid)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def create_entry(queue: Path, source: str, *, now: datetime | None = None,
                 rand: str | None = None, **fields) -> dict:
    now = now or datetime.now()
    cid = gen_id(now, rand)
    meta = new_meta(cid, source, now.astimezone().isoformat(timespec="seconds"), **fields)
    write_meta(queue, cid, meta)
    return meta


def iter_metas(queue: Path) -> list[dict]:
    """All entry metas, newest first (by id, which is timestamp-prefixed)."""
    queue = Path(queue)
    if not queue.exists():
        return []
    out: list[dict] = []
    for d in queue.iterdir():
        if not d.is_dir():
            continue
        m = read_meta(queue, d.name)
        if m:
            out.append(m)
    out.sort(key=lambda m: m.get("id", ""), reverse=True)
    return out


def read_ocr(queue: Path, cid: str) -> str:
    p = ocr_path(queue, cid)
    try:
        return p.read_text(encoding="utf-8") if p.exists() else ""
    except Exception:
        return ""


def sweep_retention(queue: Path, retention_days: int, *, now: datetime | None = None) -> int:
    """Delete terminal-state entries older than ``retention_days``. Returns count."""
    import shutil

    now = now or datetime.now()
    removed = 0
    for m in iter_metas(queue):
        if m.get("status") not in _SWEEPABLE:
            continue  # keep new/drafted/error
        created = m.get("created", "")
        try:
            age_days = (now.astimezone() - datetime.fromisoformat(created)).days
        except Exception:
            continue
        if age_days >= retention_days:
            try:
                shutil.rmtree(entry_dir(queue, m["id"]), ignore_errors=True)
                removed += 1
            except Exception:
                pass
    return removed
