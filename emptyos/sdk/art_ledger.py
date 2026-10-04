"""Cross-song art-direction ledger for the MV pipeline.

One JSONL row per art verdict, appended under ``data/art_direction/ledger.jsonl``.

## Why this exists

The MV pipeline scored `gate: strong` / `revert: strong` but `memory: absent`
(`emptyos/sdk/loops.py`). It catches faults *within* a run and forgets every
one of them *between* runs, so each song starts from zero and the same
art-direction mistake recurs across the catalogue. The 那道彩虹 "a person is
present but their identity is not assessable" lesson survived only as a code
comment in ``visual.py`` and therefore never reached the next author — who
re-derived it in prose two songs later.

This is the same "grade your own prediction" shape as
:mod:`emptyos.sdk.shape_ledger`, applied to art direction instead of geometry:
every rejection is recorded with the contract clause it violated, and
:func:`recurring_failures` feeds the *next* run's reviewer the failure modes
this catalogue keeps repeating. Rejections stop being disposable and start
compounding.

Telemetry → ``data/`` (CLAUDE.md storage split), never the vault. Best-effort:
a logging failure must never affect the caller's response, because a ledger
write is never worth failing a render over. Pure stdlib + file I/O — no kernel
import, so it unit-tests without a daemon.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# Stages that may write a verdict. `plan-review` is the cheap pre-GPU gate;
# `art-review` runs on generated stills; `human` is a person rejecting work for
# a reason no automated gate names — the friction signal that was previously
# discarded entirely.
STAGES = ("plan-review", "art-review", "human")

_LEDGER = ("art_direction", "ledger.jsonl")

# A song reaches this module under whatever name the run happened to carry, and
# the same song has arrived under two of them: `梦幻泡影` and
# `2026-02-23__梦幻泡影`. That is not cosmetic — `recurring_failures` thresholds
# on the number of *distinct* songs a code has bitten, so one song wearing two
# names clears a bar meant to need two songs. Strip the dated-folder prefix and
# case-fold; deliberately nothing more, so `08 · Log Out` and `一念之间` stay
# the distinct songs they are.
_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}__")


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def song_key(song: str) -> str:
    """Collapse the naming variants of one song onto a single identity."""
    return " ".join(_DATE_PREFIX.sub("", str(song or "")).split()).casefold()


def song_keys(rows: list[dict]) -> dict[str, str]:
    """Map every raw ``song`` in ``rows`` to its identity, repairing mangled names.

    A write through a cp1252 console turned `2026-02-23__梦幻泡影` into
    `2026-02-23__????` — one ``?`` per character. Only a name that is entirely
    ``?`` is repaired; a partly mangled one stays its own song. The name is gone, but the
    date prefix and the character count survive, so a mangled name resolves to
    the one song in the same ledger with that date and that many characters.
    Two candidates (e.g. two four-character songs on one date) or none leave the
    row as its own song: a wrong merge would hide a real second song.

    Read-time only; the stored row keeps the bytes it was written with.
    """
    by_date_len: dict[tuple[str, int], set[str]] = {}
    for row in rows:
        raw = str(row.get("song") or "")
        m = _DATE_PREFIX.match(raw)
        name = raw[m.end():] if m else ""
        if m and name and set(name) != {"?"}:
            by_date_len.setdefault((m.group(0), len(name)), set()).add(song_key(raw))
    out: dict[str, str] = {}
    for row in rows:
        raw = str(row.get("song") or "")
        if raw in out:
            continue
        m = _DATE_PREFIX.match(raw)
        name = raw[m.end():] if m else ""
        candidates = by_date_len.get((m.group(0), len(name))) if m and name and set(name) == {"?"} else None
        out[raw] = next(iter(candidates)) if candidates and len(candidates) == 1 else song_key(raw)
    return out


def _ledger_path(repo_root) -> Path:
    return Path(repo_root).joinpath("data", *_LEDGER)


def log_art_verdict(
    repo_root,
    song: str,
    scene: int | str,
    *,
    stage: str,
    verdict: str,
    hard_codes: list[str] | None = None,
    contract_clause: str = "",
    note: str = "",
    attempt_id: str = "",
    output_sha256: str = "",
) -> None:
    """Append one normalized art verdict row.

    ``contract_clause`` is the load-bearing field: a bare code like
    ``subject_scale_mismatch`` says a rule was broken, but not *which* rule, so
    it cannot teach the next run anything. Callers should pass the clause of
    the song's art direction that the still or scene contradicted.

    ``attempt_id`` (the MV library's generation record) and ``output_sha256``
    (the judged file) are optional links to evidence. They are written only when
    given — an absent link stays absent rather than an empty string that reads
    like a value — and a sha that is not 64 hex digits is not written.
    """
    try:
        codes = [str(c) for c in (hard_codes or []) if c]
        led = _ledger_path(repo_root)
        led.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "song": str(song),
            "scene": scene,
            "stage": str(stage),
            "verdict": str(verdict),
            "codes": codes,
            "contract_clause": str(contract_clause or "")[:400],
            "note": str(note or "")[:400],
        }
        if str(attempt_id or "").strip():
            row["attempt_id"] = str(attempt_id).strip()[:200]
        sha = str(output_sha256 or "").strip().lower()
        if _SHA256.match(sha):
            row["output_sha256"] = sha
        with led.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def read_rows(repo_root, *, limit: int = 5000) -> list[dict]:
    """Return ledger rows oldest-first, capped. Missing/corrupt → []."""
    try:
        led = _ledger_path(repo_root)
        if not led.exists():
            return []
        rows = []
        for line in led.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue          # one bad line must not blind the whole ledger
            if isinstance(obj, dict):
                rows.append(obj)
        return rows[-limit:]
    except Exception:
        return []


def recurring_failures(
    repo_root,
    *,
    exclude_song: str = "",
    min_songs: int = 2,
    limit: int = 6,
    evidence_limit: int = 3,
) -> list[dict]:
    """Failure codes this catalogue keeps repeating, for prompt seeding.

    ``min_songs`` (not min_count) is the threshold on purpose: one song failing
    the same way eight times is one lesson about that song, and seeding it into
    an unrelated song's reviewer would import a bias the new song never earned.
    A code only graduates to "recurring" once it has bitten **different** songs.

    ``exclude_song`` drops the run being reviewed, so a song cannot be graded
    against its own in-progress mistakes.

    Each result carries ``evidence``: the newest ``evidence_limit`` rejections
    behind the code (the row's ``song`` as written, its ``song_identity``,
    scene, stage, ts, and ``attempt_id`` / ``output_sha256`` where the row has
    them), so a reader can open what was rejected instead of trusting the count.
    """
    try:
        by_code_songs: dict[str, set] = {}
        by_code_clause: dict[str, Counter] = {}
        by_code_rows: dict[str, list[dict]] = {}
        rows = read_rows(repo_root)
        keys = song_keys(rows)
        # Resolve the excluded name on its own: adding it to `rows` would make it a
        # repair candidate and turn a mangled row back into a second song.
        exclude_key = (song_keys(rows + [{"song": exclude_song}])[str(exclude_song)]
                       if exclude_song else "")
        for row in rows:
            if str(row.get("verdict")) == "pass":
                continue
            song = keys[str(row.get("song") or "")]
            if exclude_key and song == exclude_key:
                continue
            clause = str(row.get("contract_clause") or "").strip()
            for code in (row.get("codes") or []):
                code = str(code)
                by_code_songs.setdefault(code, set()).add(song)
                by_code_rows.setdefault(code, []).append({**row, "song_identity": song})
                if clause:
                    by_code_clause.setdefault(code, Counter())[clause] += 1

        out = []
        for code, songs in by_code_songs.items():
            if len(songs) < min_songs:
                continue
            clauses = by_code_clause.get(code) or Counter()
            newest = sorted(by_code_rows[code], key=lambda r: str(r.get("ts") or ""), reverse=True)
            out.append({
                "code": code,
                "songs": len(songs),
                "example_clause": clauses.most_common(1)[0][0] if clauses else "",
                "evidence": [
                    {k: r[k] for k in ("song", "song_identity", "scene", "stage", "ts",
                                       "attempt_id", "output_sha256") if k in r}
                    for r in newest[:max(0, evidence_limit)]
                ],
            })
        out.sort(key=lambda r: (-r["songs"], r["code"]))
        return out[:limit]
    except Exception:
        return []


def recurring_failures_block(repo_root, *, exclude_song: str = "") -> str:
    """Render :func:`recurring_failures` as a prompt fragment, or "" when the
    ledger has nothing to teach yet.

    Returning "" on an empty ledger matters: it keeps the reviewer's prompt
    byte-identical to the pre-ledger prompt until real evidence exists, so this
    cannot shift review behaviour on day one (and keeps the prefix cacheable —
    `.claude/rules/prompt-prefix-cache.md`).
    """
    rows = recurring_failures(repo_root, exclude_song=exclude_song)
    if not rows:
        return ""
    lines = [
        "Recurring failure modes in this catalogue — these have already been "
        "rejected on two or more different songs. Treat them as things to look "
        "for, NOT as a reason to reject this image on its own contract:",
    ]
    for r in rows:
        line = f"- `{r['code']}` (rejected across {r['songs']} songs)"
        if r["example_clause"]:
            line += f" — e.g. “{r['example_clause']}”"
        lines.append(line)
    return "\n".join(lines)
