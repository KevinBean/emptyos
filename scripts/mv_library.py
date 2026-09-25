#!/usr/bin/env python3
"""MV prompt + asset library — record generations, record assets, count evidence.

The library lives in the vault at ``10_Projects/YouTube-Music-Channel/library/``
(see its ``README.md``). Its format is defined by two human-edited notes in that
folder, and this script reads its vocabulary FROM them rather than restating it:

* ``SCHEMA.md``        — verdicts, stages, platform→model table, modes, asset
                         kind/status/reusable, pattern status
* ``failure-codes.md`` — every allowed ``codes[]`` value

One source, so adding a model or a failure code is a note edit, never a code
edit. If either note cannot be parsed into a non-empty vocabulary the script
refuses to run (``VocabError``) — a validator that silently loaded an empty
vocabulary would accept or reject everything for the wrong reason.

Commands
--------
  record-attempt   upsert rows into attempts.jsonl (keyed by attempt_id)
  record-asset     upsert rows into assets.jsonl   (keyed by asset_id)
  stats            counts by project / model / stage / verdict / code
  refresh-patterns recompute evidence counts in patterns/*.md frontmatter

Rows come from ``--file`` (a JSON object, a JSON array, or JSONL), ``--data``
(one JSON object) or stdin. A batch is all-or-nothing: if any row is invalid or
conflicts with an existing row, nothing is written.

Re-running and reviews
----------------------
* Re-recording an identical row is a no-op.
* ``--update`` merges the new top-level fields into the stored row — the
  post-review path: submit as ``unreviewed``, later merge ``verdict`` /
  ``codes`` / ``reason`` / ``evidence`` / ``output``.
* **A review is never undone by a re-run.** When the stored row carries a real
  verdict and the incoming row says ``unreviewed``, the stored row wins every
  field it has; the incoming row can only fill fields the stored row lacks
  (and only with ``--update``). This is never a conflict, so a backfill script
  can be re-run after rows have been reviewed.
* ``--reset-review`` turns that protection off for the batch — the one way to
  put a reviewed row back to ``unreviewed``.

Writes hold a lock (a token file in the local temp dir, keyed by the library
path — deliberately NOT inside the synced vault) so two local sessions cannot
drop each other's rows. JSONL is split on ``\\n`` only: ``str.splitlines`` also
splits on U+2028/U+2029/U+0085, which ``json.dumps(ensure_ascii=False)`` writes
unescaped, so one pasted prompt would make the whole file unreadable.

stdlib only. The vault root comes from ``emptyos.toml [notes].path`` (or
``EOS_VAULT``) via ``scripts/vault_paths.py``.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

LIBRARY_REL = "10_Projects/YouTube-Music-Channel/library"
ATTEMPTS = "attempts.jsonl"
ASSETS = "assets.jsonl"

HASH_RE = re.compile(r"^[0-9a-f]{64}$")
# Verdicts that assert a reviewed outcome about an existing output.
REVIEWED_WITH_OUTPUT = {"pass", "partial", "fail", "rejected-human", "superseded"}
NEEDS_CODES = {"fail", "partial"}
NEEDS_REASON = {"fail", "partial", "rejected-human"}      # SCHEMA §3: rejected-human quotes Kevin
UNREVIEWED = "unreviewed"
PROMPT_LOST = "prompt_not_persisted"
BACKFILL = "backfill"
LIST_FIELDS_ATTEMPT = ("inputs", "codes", "pattern_ids", "used_in_cut")
LIST_FIELDS_ASSET = ("parents", "projects_used")
# Merging a row with itself must not count as a change just because it was
# re-recorded at a different moment.
VOLATILE_KEYS = {"recorded_at"}
LOCK_TIMEOUT_S = 15.0
LOCK_STALE_S = 120.0
MARK_OPEN = "<!-- mv-library:evidence -->"
MARK_CLOSE = "<!-- /mv-library:evidence -->"


class VocabError(RuntimeError):
    """SCHEMA.md / failure-codes.md did not yield a usable vocabulary."""


class RowError(ValueError):
    """Input rows could not be parsed."""


def _lines(text: str) -> list[str]:
    """Split on newline only (see module docstring); tolerate CRLF and a BOM."""
    return [ln[:-1] if ln.endswith("\r") else ln for ln in text.lstrip("\ufeff").split("\n")]


# ── vocabulary (parsed from the library's own notes) ─────────────────────────

_TICK = re.compile(r"`([^`]+)`")
_FENCE = re.compile(r"^\s*(```|~~~)")


def _sections(text: str) -> dict[str, str]:
    """Map '3' → body of '## 3. …'.

    Walks lines with fence state, so a ``## `` heading INSIDE a fenced example
    (SCHEMA §6 shows a pattern note containing ``## 用途``) neither starts nor
    ends a section.
    """
    out: dict[str, list[str]] = {}
    current: str | None = None
    fenced = False
    for line in _lines(text):
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced and line.startswith("## "):
            m = re.match(r"^## (\d+)\.", line)
            current = m.group(1) if m else None
            if current:
                out[current] = []
            continue
        if current:
            out[current].append(line)
    return {k: "\n".join(v) for k, v in out.items()}


_SEPARATOR = re.compile(r"^\|[\s:|-]+\|$")


def _table_rows(body: str) -> list[list[str]]:
    """Data rows of every markdown table in `body` — header rows excluded.

    A header is the row directly above a `|---|` separator. It must be dropped
    explicitly: SCHEMA §5's header is `` | `platform` | `model` | `` in
    backticks, so a tick-based filter would read it as a platform named
    "platform".
    """
    lines = [ln.strip() for ln in _lines(body)]
    rows = []
    for i, line in enumerate(lines):
        if not line.startswith("|") or _SEPARATOR.match(line):
            continue
        if i + 1 < len(lines) and _SEPARATOR.match(lines[i + 1]):
            continue
        rows.append([c.strip() for c in line.strip("|").split("|")])
    return rows


def _strip_asides(line: str) -> str:
    return re.sub(r"（[^）]*）|\([^)]*\)", "", line)


def _labelled_line(body: str, label: str) -> set[str]:
    """Backticked values on the line that starts with `label`：, minus the label.

    Parenthetical asides are stripped first: the status line mentions
    ``status_reason`` inside one, which is a field name, not a status.
    """
    for line in _lines(body):
        if line.startswith(f"`{label}`"):
            return set(_TICK.findall(_strip_asides(line))[1:])
    return set()


def _first_paragraph_line(body: str) -> str:
    for line in _lines(body):
        if line.strip():
            return line
    return ""


def parse_vocab(schema_text: str, codes_text: str) -> dict[str, Any]:
    sec = _sections(schema_text)
    missing = [n for n in ("2", "3", "4", "5", "6") if n not in sec]
    if missing:
        raise VocabError(f"SCHEMA.md missing sections: {missing}")

    verdicts = {
        _TICK.findall(r[0])[0] for r in _table_rows(sec["3"]) if _TICK.findall(r[0])
    }
    # Only the §4 vocabulary line: later prose in §4 may mention other words in ticks.
    stages = set(_TICK.findall(_strip_asides(_first_paragraph_line(sec["4"]))))
    platforms: dict[str, set[str]] = collections.defaultdict(set)
    for r in _table_rows(sec["5"]):
        if len(r) >= 2 and _TICK.findall(r[0]):
            platforms[_TICK.findall(r[0])[0]].update(_TICK.findall(r[1]))
    pstatus = set()
    m = re.search(r"^status:[^#\n]*#\s*(.+)$", sec["6"], re.M)
    if m:
        pstatus = {t.strip() for t in m.group(1).split("|") if t.strip()}
    codes = set()
    for r in _table_rows(codes_text):
        t = _TICK.findall(r[0]) if r else []
        if t:
            codes.add(t[0])

    vocab = {
        "verdict": verdicts,
        "stage": stages,
        "platform": dict(platforms),
        "mode": _labelled_line(sec["5"], "mode"),
        "kind": _labelled_line(sec["2"], "kind"),
        "status": _labelled_line(sec["2"], "status"),
        "reusable": _labelled_line(sec["2"], "reusable"),
        "pattern_status": pstatus,
        "codes": codes,
    }
    empty = [k for k, v in vocab.items() if not v]
    if empty:
        raise VocabError(f"could not parse vocabulary for: {empty}")
    return vocab


def load_vocab(library: Path) -> dict[str, Any]:
    try:
        schema = (library / "SCHEMA.md").read_text(encoding="utf-8")
        codes = (library / "failure-codes.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise VocabError(f"cannot read vocabulary notes: {e}") from e
    return parse_vocab(schema, codes)


# ── validation ───────────────────────────────────────────────────────────────

def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _in(v: Any, allowed: Any) -> bool:
    """Membership that treats a non-string (list, dict, number) as not allowed."""
    return isinstance(v, str) and v in allowed


def as_list(v: Any) -> list:
    """A list field's value, or [] for anything that is not a list (validators report those)."""
    return v if isinstance(v, list) else []


def _check_lists(row: dict, fields: Iterable[str], out: list) -> None:
    for f in fields:
        if f in row and row[f] is not None and not isinstance(row[f], list):
            out.append(("not_list", f"{f} must be a list or null, got {type(row[f]).__name__}"))


def _check_path(rel: Any, field: str, vault: Path | None, out: list) -> None:
    if rel is None:
        return
    if not isinstance(rel, str) or not rel.strip():
        out.append(("bad_path", f"{field}: must be a non-empty string or null"))
        return
    if ("\\" in rel or rel.startswith("/") or re.match(r"^[A-Za-z]:", rel)
            or ".." in rel.split("/")):
        out.append(("bad_path", f"{field}: must be vault-relative, forward slashes, no '..': {rel}"))
        return
    if vault is not None and not (vault / rel).exists():
        out.append(("path_missing", f"{field}: does not resolve under the vault: {rel}"))


def _check_hash(h: Any, field: str, out: list) -> None:
    if h is not None and not (isinstance(h, str) and HASH_RE.match(h)):
        out.append(("bad_hash", f"{field}: not a lowercase 64-hex sha256: {h!r}"))


def _has_key(obj: Any, key: str) -> bool:
    if isinstance(obj, dict):
        return key in obj or any(_has_key(v, key) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_key(v, key) for v in obj)
    return False


def _vocab_check(row: dict, field: str, allowed: Any, where: str, out: list) -> None:
    v = row.get(field)
    if not _blank(v) and not _in(v, allowed):
        out.append(("bad_vocab", f"{field} {v!r} not in {where}"))


def validate_attempt(
    row: Any, vocab: dict, vault: Path | None, *, patterns: set[str] | None = None
) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if not isinstance(row, dict):
        return [("not_object", "row is not a JSON object")]
    for f in ("attempt_id", "project", "project_path", "stage", "platform", "model", "mode", "verdict"):
        if _blank(row.get(f)):
            out.append(("missing_field", f"{f} is required"))
    if not _blank(row.get("attempt_id")) and not isinstance(row.get("attempt_id"), str):
        out.append(("bad_id", "attempt_id must be a string"))
    if "prompt" not in row:
        out.append(("missing_field", "prompt is required (null when lost)"))
    _check_lists(row, LIST_FIELDS_ATTEMPT, out)

    # A non-string verdict is reported by the vocab check below; past that point
    # it must not reach set membership (`["pass"] in {...}` raises).
    verdict = row.get("verdict") if isinstance(row.get("verdict"), str) else None
    platform, model = row.get("platform"), row.get("model")
    _vocab_check(row, "verdict", vocab["verdict"], "SCHEMA §3", out)
    _vocab_check(row, "stage", vocab["stage"], "SCHEMA §4", out)
    if not _blank(platform) and not _in(platform, vocab["platform"]):
        out.append(("bad_vocab", f"platform {platform!r} not in SCHEMA §5"))
    elif not _blank(model) and not _in(model, vocab["platform"].get(platform, ())):
        out.append(("bad_vocab", f"model {model!r} not listed for platform {platform!r}"))
    _vocab_check(row, "mode", vocab["mode"], "SCHEMA §5", out)

    codes = as_list(row.get("codes"))
    for c in codes:
        if not _in(c, vocab["codes"]):
            out.append(("bad_vocab", f"code {c!r} not in failure-codes.md"))

    prompt = row.get("prompt")
    if "prompt" in row:
        if prompt is None:
            if PROMPT_LOST not in codes:
                out.append(("prompt_missing", f"prompt is null without code {PROMPT_LOST}"))
        elif not isinstance(prompt, str) or not prompt.strip():
            out.append(("prompt_missing", "prompt must be non-empty text, or null"))
    _check_hash(row.get("prompt_sha256"), "prompt_sha256", out)
    if isinstance(prompt, str) and prompt.strip() and row.get("prompt_sha256"):
        if row["prompt_sha256"] != sha256_text(prompt):
            out.append(("bad_hash", "prompt_sha256 does not match prompt"))

    if verdict in NEEDS_CODES and not codes:
        out.append(("needs_codes", f"verdict {verdict} requires codes[]"))
    if verdict in NEEDS_REASON and _blank(row.get("reason")):
        out.append(("needs_reason", f"verdict {verdict} requires reason"))
    output = row.get("output")
    if output is not None and not isinstance(output, dict):
        out.append(("needs_output", "output must be an object or null"))
    if verdict in REVIEWED_WITH_OUTPUT:
        if not (isinstance(output, dict) and (output.get("path") or output.get("sha256"))):
            out.append(("needs_output", f"verdict {verdict} requires output with a path or sha256"))
        if not isinstance(row.get("evidence"), dict) or _blank(row["evidence"].get("path")):
            out.append(("needs_evidence", f"verdict {verdict} requires evidence.path"))
    if verdict == "generation-failed" and output:
        out.append(("needs_output", "generation-failed must have output: null"))

    _check_path(row.get("project_path"), "project_path", vault, out)
    for i, inp in enumerate(as_list(row.get("inputs"))):
        if not isinstance(inp, dict):
            out.append(("not_object", f"inputs[{i}] is not an object"))
            continue
        _check_hash(inp.get("sha256"), f"inputs[{i}].sha256", out)
        _check_path(inp.get("path"), f"inputs[{i}].path", vault, out)
        if inp.get("sha256") is None and _blank(row.get("reason")) and _blank(inp.get("note")):
            out.append(("needs_reason", f"inputs[{i}].sha256 is null without a reason or inputs[{i}].note"))
    if isinstance(output, dict):
        _check_hash(output.get("sha256"), "output.sha256", out)
        _check_path(output.get("path"), "output.path", vault, out)
    if isinstance(row.get("evidence"), dict):
        _check_path(row["evidence"].get("path"), "evidence.path", vault, out)
    src = row.get("source")
    if row.get("recorded_by") == BACKFILL and not (isinstance(src, dict) and src.get("file")):
        out.append(("missing_field", "backfill rows require source.file"))
    if isinstance(src, dict):
        _check_path(src.get("file"), "source.file", vault, out)

    credits = row.get("credits")
    if credits is not None and not isinstance(credits, dict):
        out.append(("bad_credits", "credits must be an object or null"))
    elif isinstance(credits, dict):
        for k, v in credits.items():
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))):
                out.append(("bad_credits", f"credits.{k} must be a number or null: {v!r}"))
    if patterns is not None:
        for p in as_list(row.get("pattern_ids")):
            if not _in(p, patterns):
                out.append(("unknown_pattern", f"pattern_id {p!r} has no patterns/<id>.md"))
    if _has_key(row, "lyrics"):
        out.append(("lyrics_stored", "rows must not carry a lyrics field"))
    return out


def validate_asset(
    row: Any, vocab: dict, vault: Path | None, *, attempt_ids: set[str] | None = None,
    asset_ids: set[str] | None = None,
) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if not isinstance(row, dict):
        return [("not_object", "row is not a JSON object")]
    for f in ("asset_id", "kind", "path", "status", "status_reason"):
        if _blank(row.get(f)):
            out.append(("missing_field", f"{f} is required"))
    if not _blank(row.get("asset_id")) and not isinstance(row.get("asset_id"), str):
        out.append(("bad_id", "asset_id must be a string"))
    for f in ("origin", "rights"):
        if not isinstance(row.get(f), dict):
            out.append(("missing_field", f"{f} is required (object)"))
    if "sha256" not in row:
        out.append(("missing_field", "sha256 is required (null when unknown)"))
    _check_lists(row, LIST_FIELDS_ASSET, out)
    for f in ("kind", "status", "reusable"):
        _vocab_check(row, f, vocab[f], "SCHEMA §2", out)
    _check_hash(row.get("sha256"), "sha256", out)
    _check_path(row.get("path"), "path", vault, out)
    origin = row.get("origin") if isinstance(row.get("origin"), dict) else {}
    op, om = origin.get("platform"), origin.get("model")
    if op is not None and not _in(op, vocab["platform"]):
        out.append(("bad_vocab", f"origin.platform {op!r} not in SCHEMA §5"))
    elif op is not None and om is not None and not _in(om, vocab["platform"][op]):
        out.append(("bad_vocab", f"origin.model {om!r} not listed for {op!r}"))
    oid = origin.get("attempt_id")
    if attempt_ids is not None and oid and not _in(oid, attempt_ids):
        out.append(("dangling_ref", f"origin.attempt_id {oid!r} not in attempts.jsonl"))
    if asset_ids is not None:
        for p in as_list(row.get("parents")):
            if not _in(p, asset_ids):
                out.append(("dangling_ref", f"parent {p!r} not in assets.jsonl"))
    if _has_key(row, "lyrics"):
        out.append(("lyrics_stored", "rows must not carry a lyrics field"))
    return out


# ── storage ──────────────────────────────────────────────────────────────────

def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def read_jsonl(path: Path) -> list[Any]:
    """Values of a JSONL file (not only objects — validators report those).

    RowError names the first unparseable line, including a decode failure.
    """
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise RowError(f"{path.name}: not UTF-8: {e}") from e
    rows = []
    for n, line in enumerate(_lines(text), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise RowError(f"{path.name}:{n}: {e}") from e
    return rows


def objects(rows: Iterable[Any]) -> list[dict]:
    return [r for r in rows if isinstance(r, dict)]


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_jsonl_atomic(path: Path, rows: Iterable[Any]) -> None:
    write_text_atomic(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def lock_path(target: Path) -> Path:
    """Local (unsynced) lock location for a library file."""
    key = hashlib.sha256(str(target.resolve()).lower().encode("utf-8")).hexdigest()[:24]
    return Path(tempfile.gettempdir()) / "mv-library-locks" / f"{key}.lock"


def _read_token(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


@contextmanager
def file_lock(target: Path, timeout: float | None = None) -> Iterator[None]:
    """Exclusive, token-owned lock for `target`.

    * Acquire: exclusive create of the lock file holding a unique token.
    * Release: delete only if the file still holds OUR token — never another
      writer's lock.
    * Stale (older than LOCK_STALE_S at call time): break by renaming the file
      aside, then keep the renamed file only if its token is the stale one we
      observed; a fresh lock caught mid-race is renamed back.
    `timeout` defaults to LOCK_TIMEOUT_S read at call time.
    """
    lock = lock_path(target)
    lock.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    deadline = time.monotonic() + (LOCK_TIMEOUT_S if timeout is None else timeout)
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(token)
            break
        except FileExistsError:
            seen = _read_token(lock)
            try:
                age = time.time() - lock.stat().st_mtime
            except FileNotFoundError:
                continue
            if age > LOCK_STALE_S and seen is not None:
                aside = lock.with_name(f"{lock.name}.{token}.stale")
                try:
                    os.replace(lock, aside)
                except FileNotFoundError:
                    continue
                if _read_token(aside) == seen:
                    aside.unlink(missing_ok=True)
                else:                                   # took a fresh lock: give it back
                    try:
                        os.link(aside, lock)
                    except OSError:
                        pass
                    aside.unlink(missing_ok=True)
                continue
            if time.monotonic() > deadline:
                raise TimeoutError(f"library is locked by another writer: {lock}")
            time.sleep(0.1)
    try:
        yield
    finally:
        if _read_token(lock) == token:
            # Windows refuses to delete a file another process/thread has open
            # (a waiter reading the token): retry briefly instead of failing.
            for _ in range(50):
                try:
                    lock.unlink(missing_ok=True)
                    break
                except PermissionError:
                    time.sleep(0.02)


def parse_rows(text: str) -> list[Any]:
    text = text.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else [data]
    except json.JSONDecodeError:
        pass
    rows = []
    for n, line in enumerate(_lines(text), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise RowError(f"input line {n}: {e}") from e
    return rows


def _comparable(row: dict) -> dict:
    return {k: v for k, v in row.items() if k not in VOLATILE_KEYS}


def _keeps_review(old: dict, new: dict) -> bool:
    return new.get("verdict") == UNREVIEWED and old.get("verdict") not in (None, UNREVIEWED)


def fill_attempt(row: dict, now: str) -> dict:
    """Derive the fields a caller should not have to compute by hand."""
    row = dict(row)
    if _blank(row.get("attempt_id")) and not any(
        _blank(row.get(k)) for k in ("project", "stage", "shot", "attempt")
    ):
        row["attempt_id"] = f"{row['project']}:{row['stage']}:{row['shot']}:{row['attempt']}"
    if isinstance(row.get("prompt"), str) and row["prompt"].strip() and not row.get("prompt_sha256"):
        row["prompt_sha256"] = sha256_text(row["prompt"])
    row.setdefault("recorded_at", now)
    return row


def upsert(stored: list[dict], incoming: list[dict], key: str, *, update: bool,
           reset_review: bool = False) -> dict:
    """Plan an upsert. Returns {rows, added, updated, unchanged, conflicts}."""
    index: dict[str, int] = {}
    for i, r in enumerate(stored):
        if isinstance(r.get(key), str):
            index[r[key]] = i
    rows = list(stored)
    added = updated = unchanged = 0
    conflicts: list[str] = []
    seen: set[str] = set()
    for r in incoming:
        k = r.get(key)
        if not isinstance(k, str):
            conflicts.append(f"{key} {k!r} is not a string")
            continue
        if k in seen:
            conflicts.append(f"{k}: appears twice in this batch")
            continue
        seen.add(k)
        if k not in index:
            index[k] = len(rows)
            rows.append(r)
            added += 1
            continue
        old = rows[index[k]]
        protect = _keeps_review(old, r) and not reset_review
        if protect:
            merged = {**r, **old} if update else old      # stored review wins every field it has
        else:
            merged = {**old, **r} if update else r
        if _comparable(merged) == _comparable(old):
            unchanged += 1
        elif update:
            merged["recorded_at"] = old.get("recorded_at", r.get("recorded_at"))
            rows[index[k]] = merged
            updated += 1
        else:
            conflicts.append(f"{k}: already recorded with different content (use --update)")
    return {"rows": rows, "added": added, "updated": updated,
            "unchanged": unchanged, "conflicts": conflicts}


def _frontmatter(text: str) -> tuple[str, str]:
    text = text.lstrip("\ufeff").replace("\r\n", "\n")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    return (m.group(1), text[m.end():]) if m else ("", text)


def pattern_ids(library: Path) -> set[str]:
    """pattern_id of every readable note; unreadable notes are skipped here and reported by the checker."""
    ids = set()
    for p in sorted((library / "patterns").glob("*.md")):
        try:
            fm = _frontmatter(p.read_text(encoding="utf-8"))[0]
        except (OSError, UnicodeDecodeError):
            continue
        m = re.search(r"^pattern_id:\s*(\S+)", fm, re.M)
        if m:
            ids.add(m.group(1))
    return ids


def record(
    library: Path, vault: Path | None, incoming: list[Any], *, kind: str, update: bool = False,
    reset_review: bool = False, now: str | None = None,
) -> dict:
    """Validate + upsert a batch under the lock. Writes nothing unless every row is clean."""
    vocab = load_vocab(library)
    now = now or datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    if kind == "attempt":
        path, key = library / ATTEMPTS, "attempt_id"
        incoming = [fill_attempt(r, now) if isinstance(r, dict) else r for r in incoming]
    else:
        path, key = library / ASSETS, "asset_id"
        incoming = [({**r, "recorded_at": r.get("recorded_at", now)} if isinstance(r, dict) else r)
                    for r in incoming]
    errors: list[dict] = [{"id": None, "code": "not_object", "message": "row is not a JSON object"}
                          for r in incoming if not isinstance(r, dict)]
    with file_lock(path):
        stored = read_jsonl(path)
        errors += [{"id": None, "code": "not_object", "message": f"stored {path.name} row is not an object"}
                   for r in stored if not isinstance(r, dict)]
        plan = upsert(objects(stored), objects(incoming), key, update=update, reset_review=reset_review)
        final = plan["rows"]
        if kind == "attempt":
            pats = pattern_ids(library)
        else:
            attempt_ids = {r.get("attempt_id") for r in objects(read_jsonl(library / ATTEMPTS))
                           if isinstance(r.get("attempt_id"), str)}
            asset_ids = {r.get(key) for r in final if isinstance(r.get(key), str)}
        touched = {r.get(key) for r in objects(incoming) if isinstance(r.get(key), str)}
        for r in final:
            if r.get(key) not in touched:
                continue
            found = (validate_attempt(r, vocab, vault, patterns=pats) if kind == "attempt"
                     else validate_asset(r, vocab, vault, attempt_ids=attempt_ids, asset_ids=asset_ids))
            errors += [{"id": r.get(key), "code": c, "message": m} for c, m in found]
        ok = not errors and not plan["conflicts"]
        written = ok and bool(plan["added"] or plan["updated"])
        if written:
            write_jsonl_atomic(path, final)
    return {"ok": ok, "written": written,
            "added": plan["added"], "updated": plan["updated"], "unchanged": plan["unchanged"],
            "conflicts": plan["conflicts"], "errors": errors}


# ── stats + pattern evidence ────────────────────────────────────────────────

def stats(attempts: list[Any]) -> dict:
    rows = objects(attempts)
    by = {k: collections.Counter() for k in ("project", "model", "stage", "verdict", "code")}
    for r in rows:
        for k in ("project", "model", "stage", "verdict"):
            by[k][str(r.get(k) or "unknown")] += 1
        for c in as_list(r.get("codes")):
            by["code"][str(c)] += 1
    return {"total": len(rows), **{k: dict(v.most_common()) for k, v in by.items()}}


def pattern_evidence(attempts: list[Any], pid: str) -> dict:
    counts = {"pass": 0, "partial": 0, "fail": 0}
    projects: set[str] = set()
    ids: dict[str, list[str]] = collections.defaultdict(list)
    for r in objects(attempts):
        if pid not in as_list(r.get("pattern_ids")):      # a string would substring-match
            continue
        v = str(r.get("verdict"))
        ids[v].append(str(r.get("attempt_id")))
        if v in counts:
            counts[v] += 1
        if v == "pass":
            projects.add(str(r.get("project")))
    return {**counts, "pass_projects": len(projects), "ids": dict(ids)}


def _set_fm_line(fm: str, key: str, value: str) -> str:
    """Set `key: value`; replaces a flow, block-mapping or block-list value and keeps a trailing comment."""
    lines = fm.split("\n")
    for i, line in enumerate(lines):
        if re.match(rf"^{re.escape(key)}:", line):
            comment = re.search(r"\s+#.*$", line)
            j = i + 1
            while j < len(lines) and re.match(r"^([ \t]+\S|- |-$)", lines[j]):
                j += 1
            return "\n".join(lines[:i] + [f"{key}: {value}{comment.group(0) if comment else ''}"] + lines[j:])
    return f"{fm}\n{key}: {value}"


def _replace_evidence_block(body: str, content: str) -> str:
    """Rewrite the first marker pair that sits outside a code fence; leave quoted examples."""
    lines = body.split("\n")
    fenced = False
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced and line.strip() == MARK_OPEN:
            for j in range(i + 1, len(lines)):
                if lines[j].strip() == MARK_CLOSE:
                    return "\n".join(lines[: i + 1] + content.split("\n") + lines[j:])
            return body
    return body


def refresh_pattern_text(text: str, attempts: list[Any], today: str) -> str:
    """Rewrite evidence counts. Line endings: an all-CRLF note stays CRLF; a
    mixed note is normalised to LF (there is no faithful single choice)."""
    bare = text.lstrip("\ufeff")
    bom = text[: len(text) - len(bare)]
    crlf = "\r\n" in bare and "\n" not in bare.replace("\r\n", "")
    fm, body = _frontmatter(bare)
    m = re.search(r"^pattern_id:\s*(\S+)", fm, re.M)
    if not m:
        return text
    ev = pattern_evidence(attempts, m.group(1))
    value = "{pass: %d, partial: %d, fail: %d, pass_projects: %d}" % (
        ev["pass"], ev["partial"], ev["fail"], ev["pass_projects"])
    lines = [f"- {v}: " + ", ".join(f"`{i}`" for i in sorted(ids))
             for v, ids in sorted(ev["ids"].items())] or ["- （尚無生成記錄）"]
    new_body = _replace_evidence_block(body, "\n".join(lines))
    new_fm = _set_fm_line(fm, "evidence", value)
    if new_fm == fm and new_body == body:
        return text                          # nothing moved: keep the old refresh date
    new_fm = _set_fm_line(new_fm, "evidence_refreshed", today)
    out = f"---\n{new_fm}\n---\n{new_body}"
    return bom + (out.replace("\n", "\r\n") if crlf else out)


def refresh_patterns(library: Path, *, today: str | None = None, dry_run: bool = False) -> list[str]:
    today = today or datetime.date.today().isoformat()
    attempts = read_jsonl(library / ATTEMPTS)
    changed = []
    for p in sorted((library / "patterns").glob("*.md")):
        old = p.read_text(encoding="utf-8")
        new = refresh_pattern_text(old, attempts, today)
        if new != old:
            changed.append(p.name)
            if not dry_run:
                write_text_atomic(p, new)
    return changed


# ── CLI ──────────────────────────────────────────────────────────────────────

def resolve(args) -> tuple[Path | None, Path | None]:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from vault_paths import vault_root  # noqa: E402 — scripts/ sibling

    vault = Path(args.vault) if args.vault else vault_root()
    library = Path(args.library) if args.library else (vault / LIBRARY_REL if vault else None)
    return vault, library


def _read_input(args) -> list[Any]:
    if args.data:
        return parse_rows(args.data)
    if args.file:
        return parse_rows(Path(args.file).read_text(encoding="utf-8"))
    return parse_rows(sys.stdin.read())


def _emit(ok: bool, code: str, message: str, data: Any, as_json: bool, human) -> int:
    if as_json:
        from scanner_lib import emit_json
        return emit_json(ok, code, message, data)
    human()
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", default="", help="vault root (default: emptyos.toml)")
    ap.add_argument("--library", default="", help="library dir (default: <vault>/" + LIBRARY_REL + ")")
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("record-attempt", "record-asset"):
        s = sub.add_parser(name)
        s.add_argument("--file", default="")
        s.add_argument("--data", default="")
        s.add_argument("--update", action="store_true",
                       help="merge fields into an existing row instead of refusing")
        s.add_argument("--reset-review", action="store_true",
                       help="allow an 'unreviewed' row to replace a reviewed one")
    sub.add_parser("stats")
    r = sub.add_parser("refresh-patterns")
    r.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    vault, library = resolve(args)
    if library is None or not library.is_dir():
        msg = f"no library at {library}" if library else "no vault configured"
        return _emit(False, "no_library", msg, None, args.json, lambda: print(msg))

    try:
        if args.cmd in ("record-attempt", "record-asset"):
            kind = "attempt" if args.cmd == "record-attempt" else "asset"
            res = record(library, vault, _read_input(args), kind=kind, update=args.update,
                         reset_review=args.reset_review)
            msg = (f"added {res['added']} · updated {res['updated']} · unchanged {res['unchanged']}"
                   f" · conflicts {len(res['conflicts'])} · errors {len(res['errors'])}"
                   + ("" if res["written"] or not res["ok"] else " (nothing to write)")
                   + ("" if res["ok"] else " — NOTHING WRITTEN"))

            def human():
                print(msg)
                for c in res["conflicts"]:
                    print(f"  [conflict] {c}")
                for e in res["errors"]:
                    print(f"  [{e['code']}] {e['id']}: {e['message']}")
            return _emit(res["ok"], "invalid_rows", msg, res, args.json, human)

        if args.cmd == "stats":
            st = stats(read_jsonl(library / ATTEMPTS))

            def human():
                print(f"{st['total']} attempts")
                for k in ("project", "model", "stage", "verdict", "code"):
                    print(f"\n{k}:")
                    for name, n in list(st[k].items())[:15]:
                        print(f"  {n:>5}  {name}")
            return _emit(True, "ok", f"{st['total']} attempts", st, args.json, human)

        changed = refresh_patterns(library, dry_run=args.dry_run)
        verb = "would change" if args.dry_run else "refreshed"
        msg = f"{verb} {len(changed)} pattern note(s)"
        ok = not (args.dry_run and changed)
        return _emit(ok, "stale_evidence", msg, {"changed": changed}, args.json,
                     lambda: print(msg + ("".join(f"\n  {c}" for c in changed))))
    except (VocabError, RowError, OSError, TimeoutError, UnicodeDecodeError) as e:
        code = {VocabError: "vocab", RowError: "bad_input"}.get(type(e), "io_error")
        err = str(e)                         # `e` is unbound once the except block ends
        return _emit(False, code, err, None, args.json, lambda: print(f"error: {err}"))


if __name__ == "__main__":
    sys.exit(main())
