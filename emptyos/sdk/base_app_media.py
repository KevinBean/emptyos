"""BaseApp media family — file serving + saved-artifact helpers.

Extracted from base_app.py to keep the BaseApp spine atomic (P4 Atomic,
CLAUDE.md rule 4). Source split only — every function here is re-bound onto
``BaseApp`` in base_app.py's class body, so the public API (``self.serve_*``,
``self.save_*``) is unchanged. Owns: audio/data file serving from ``data/``,
audio-upload persistence, and the shared calculation-record / report-note
"save result to vault" pattern for calculators.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: no cross-module reach (spine helpers via ``self``).
Do not import from ``.base_app`` (it imports us, which would cycle).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk.utils import now_iso

if TYPE_CHECKING:
    from .base_app import BaseApp  # noqa: F401 — for type hints only


# ─── Bind to BaseApp class as ────────────────────────────────────────
#   serve_shared_audio = _media.serve_shared_audio
#   save_audio_upload  = _media.save_audio_upload
#   serve_data_file    = _media.serve_data_file
#   serve_audio_file   = _media.serve_audio_file
#   save_calculation   = _media.save_calculation
#   save_report_note   = _media.save_report_note
#   list_calculations  = _media.list_calculations
# Adding a new method here? Add a matching binding line in base_app.py.
# ─────────────────────────────────────────────────────────────────────


def serve_shared_audio(self, filename: str):
    """Return a ``FileResponse`` for a TTS-generated file in the shared
    ``emptyos-voice`` temp dir.

    Every app that owns a ``GET /api/audio/{filename}`` route (assistant,
    reader, radio, dictionary, …) was hand-rolling the same path-traversal
    guards + MIME lookup. This collapses the four copies into one.
    Returns a starlette ``Response`` either way — callers wrap with
    ``@web_route``.
    """
    from starlette.responses import FileResponse, JSONResponse

    from emptyos.capabilities.audio import AUDIO_DIR, AUDIO_MIME

    if "/" in filename or "\\" in filename or ".." in filename:
        return JSONResponse({"error": "invalid filename"}, status_code=400)
    path = (AUDIO_DIR / filename).resolve()
    if not str(path).startswith(str(AUDIO_DIR.resolve())):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    if not path.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    mime = AUDIO_MIME.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(str(path), media_type=mime)


# --- Audio upload/serve helpers (used by voice-input apps) ---


async def save_audio_upload(
    self, upload_file, *, subdir: str = "audio", prefix: str = "rec"
) -> tuple[Path, str]:
    """Persist a multipart-uploaded audio blob under data_dir/{subdir}.

    Accepts a Starlette UploadFile (or anything with an async `.read()` and
    an optional `.filename`). Returns (absolute_path, filename). Filename
    is randomized with the original suffix preserved (default .webm).
    """
    import uuid

    audio_dir = self.data_dir / subdir
    audio_dir.mkdir(parents=True, exist_ok=True)
    ext = ".webm"
    orig = getattr(upload_file, "filename", None)
    if orig:
        ext = Path(orig).suffix or ext
    filename = f"{prefix}_{uuid.uuid4().hex[:12]}{ext}"
    filepath = audio_dir / filename
    filepath.write_bytes(await upload_file.read())
    return filepath, filename


def serve_data_file(
    self,
    subdir: str,
    *parts: str,
    media_type: str = "application/octet-stream",
    download_name: str = "",
):
    """Serve a file under ``data_dir/{subdir}/{*parts}``.

    Path-traversal-safe via two checks: any segment containing ``/`` or
    ``\\`` is rejected up-front (those split a single component into
    many — almost always a routing error), then the fully resolved
    target must still sit under the resolved ``data_dir/{subdir}``
    root. The latter catches ``..`` traversals, symlink escapes, and
    any future filesystem escape vector — without rejecting legitimate
    filenames that happen to contain ``..`` as a substring (e.g.
    ``file..tar.gz``).

    Returns 400 ``{"error": "invalid path"}`` on a routing-shape
    violation or a resolved-out-of-root attempt, 404 on a missing
    file. Use this for serving generated artifacts (screenshots,
    exports) through a route shaped like ``/api/<thing>/{ts}/{name}``.
    """
    from starlette.responses import FileResponse, JSONResponse

    for p in parts:
        if "/" in p or "\\" in p:
            return JSONResponse({"error": "invalid path"}, status_code=400)
    root = (self.data_dir / subdir).resolve()
    target = root
    for p in parts:
        target = target / p
    try:
        target = target.resolve()
        target.relative_to(root)
    except (ValueError, OSError):
        return JSONResponse({"error": "invalid path"}, status_code=400)
    if not target.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    # Without an explicit filename the browser names the download from the URL
    # path, so a route like `/api/timesheet.pdf` always saves as
    # "timesheet.pdf" no matter what the generated file is called — two exports
    # covering different periods land on top of each other in Downloads.
    if download_name:
        return FileResponse(str(target), media_type=media_type, filename=download_name)
    return FileResponse(str(target), media_type=media_type)


def serve_audio_file(self, filename: str, *, subdir: str = "audio"):
    """Return a Starlette FileResponse for data_dir/{subdir}/{filename}.

    Returns a 404 JSONResponse when the file is missing. Content-Type is
    inferred from the file extension.
    """
    from starlette.responses import FileResponse, JSONResponse

    filepath = self.data_dir / subdir / filename
    if not filepath.exists() or not filepath.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    content_types = {
        ".wav": "audio/wav",
        ".webm": "audio/webm",
        ".mp3": "audio/mpeg",
        ".mp4": "audio/mp4",
        ".ogg": "audio/ogg",
        ".m4a": "audio/mp4",
    }
    ct = content_types.get(filepath.suffix.lower(), "audio/webm")
    return FileResponse(str(filepath), media_type=ct)


# --- Calculation records (shared "save result" pattern for calculators) ---


def save_calculation(
    self,
    *,
    label: str,
    inputs: dict,
    result: dict,
    method: str | None = None,
    extra: dict | None = None,
) -> dict:
    """Persist a one-off calculation to the app's vault as a model note.

    Shared "save calculation record" pattern for deterministic calculator
    apps (cable-stress, hv-clearance, short-circuit, cable-discharge, ...).
    Writes ``30_Resources/EmptyOS/<app>/calculations/<id>.md`` via
    :func:`serialize_model_note` (tag ``calculation``), giving every
    calculator an audit trail without per-app storage code. Compute-and-show
    stays the default — saving is an explicit user action. The write is
    reversible/internal (the note itself is the record + the undo).

    Returns ``{"ok", "id", "app", "path"}`` where ``path`` is
    vault-root-relative — feed it to ``EOS.noteActions(path)`` for a
    clickable view/edit link.
    """
    from emptyos.sdk.model_note import (
        serialize_model_note,
        slugify_id,
        now_iso,
    )

    ts = now_iso()
    stamp = "".join(ch for ch in ts if ch.isdigit())[:14]
    cid = f"{slugify_id(label or 'calc')}-{stamp}"
    record = {
        "id": cid,
        "title": label or "Calculation",
        "method": method,
        "inputs": inputs or {},
        "result": result or {},
        "created": ts,
        "author": "user",
    }
    if extra:
        record.update(extra)
    extra_fm = {"app": self.manifest.id}
    if method:
        extra_fm["method"] = method
    note = serialize_model_note(
        record,
        tag="calculation",
        intro=f"Saved {self.manifest.id} calculation.",
        extra_frontmatter=extra_fm,
    )
    rel = f"calculations/{cid}.md"
    self.vault_write(rel, note)
    return {
        "ok": True,
        "id": cid,
        "app": self.manifest.id,
        "path": self.vault_rel(self.vault_path(rel))
        or f"30_Resources/EmptyOS/{self.manifest.id}/{rel}",
    }


def save_report_note(
    self,
    filename: str,
    *,
    title: str,
    body: str,
    tags: list[str] | None = None,
    as_of: str | None = None,
    extra: dict | None = None,
) -> str:
    """Write an AI-authored snapshot report under this app's ``outputs/`` folder.

    Shared "AI report → ``outputs/`` snapshot note" pattern (expense/finance
    monthly reports, braindump summaries). Stamps the authorship-boundary
    convention — ``author: ai``, ``lifecycle: snapshot``, ``as_of`` — onto
    the frontmatter and indexes the note via :meth:`vault_create_note`, so
    every report lands under ``30_Resources/EmptyOS/<app>/outputs/`` looking
    the same. The caller owns the emit + return shape (event names and
    payloads are genuinely app-specific).

    Args:
        filename: note filename within ``outputs/``
            (e.g. ``"2026-07-expense-report.md"``).
        title: frontmatter ``title``.
        body: markdown body, already composed by the caller.
        tags: frontmatter tags (e.g. ``["expense", "report"]``); defaults to
            ``["report"]``.
        as_of: snapshot date (ISO); defaults to today.
        extra: app-specific frontmatter fields merged in last
            (``month``, ``total``, ``net_worth_aud``, ``run_id`` …).

    Returns the vault-root-relative path — feed it to ``EOS.noteActions(path)``
    or an emit payload.

    Not for: user-authored notes (this hardcodes ``author: ai``),
    non-snapshot living notes, or writes outside the app's ``outputs/`` folder.
    """
    from datetime import date as _date

    rel = self.vault_rel(self.vault_path(f"outputs/{filename}")) or (
        f"30_Resources/EmptyOS/{self.manifest.id}/outputs/{filename}"
    )
    frontmatter = {
        "title": title,
        "tags": tags or ["report"],
        "author": "ai",
        "lifecycle": "snapshot",
        "as_of": as_of or _date.today().isoformat(),
    }
    if extra:
        frontmatter.update(extra)
    self.vault_create_note(rel, frontmatter, body)
    return rel


def list_calculations(self, *, limit: int = 50) -> list[dict]:
    """Recent saved calculations, newest first (read side of
    :meth:`save_calculation`).

    Returns ``{id, title, method, created, result, path}`` summaries; the
    full ``inputs`` block stays in the note. ``path`` is vault-root-relative
    for ``EOS.noteActions``.
    """
    from emptyos.sdk.model_note import parse_model_note

    out = []
    for f in self.vault_list("calculations/*.md"):
        model = parse_model_note(
            f.read_text(encoding="utf-8", errors="ignore")
        ) or {}
        row = {
            "id": model.get("id") or f.stem,
            "title": model.get("title") or f.stem,
            "method": model.get("method"),
            "created": model.get("created") or "",
            "result": model.get("result"),
            "path": self.vault_rel(f),
        }
        # Optional engineering-evidence fields. Older calculation notes omit
        # them and retain the historical response shape.
        for key in (
            "source_entity", "source_updated", "analysis", "model_hash",
            "inputs_hash", "method_version", "standards", "warnings",
            "result_summary",
            # Solver-inputs-only staleness key, distinct from inputs_hash/model_hash
            # (which hash the whole evidence model, results included). A live
            # recompute loop probes against this; without it on the READ side the
            # run always looks stale and the loop never terminates.
            "input_signature",
        ):
            if key in model:
                row[key] = model[key]
        out.append(row)
    out.sort(key=lambda r: r.get("created") or "", reverse=True)
    return out[:limit]
