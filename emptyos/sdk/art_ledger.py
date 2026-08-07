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


def song_key(song: str) -> str:
    """Collapse the naming variants of one song onto a single identity."""
    return " ".join(_DATE_PREFIX.sub("", str(song or "")).split()).casefold()


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
) -> None:
    """Append one normalized art verdict row.

    ``contract_clause`` is the load-bearing field: a bare code like
    ``subject_scale_mismatch`` says a rule was broken, but not *which* rule, so
    it cannot teach the next run anything. Callers should pass the clause of
    the song's art direction that the still or scene contradicted.
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
) -> list[dict]:
    """Failure codes this catalogue keeps repeating, for prompt seeding.

    ``min_songs`` (not min_count) is the threshold on purpose: one song failing
    the same way eight times is one lesson about that song, and seeding it into
    an unrelated song's reviewer would import a bias the new song never earned.
    A code only graduates to "recurring" once it has bitten **different** songs.

    ``exclude_song`` drops the run being reviewed, so a song cannot be graded
    against its own in-progress mistakes.
    """
    try:
        by_code_songs: dict[str, set] = {}
        by_code_clause: dict[str, Counter] = {}
        exclude_key = song_key(exclude_song)
        for row in read_rows(repo_root):
            if str(row.get("verdict")) == "pass":
                continue
            song = song_key(row.get("song") or "")
            if exclude_key and song == exclude_key:
                continue
            clause = str(row.get("contract_clause") or "").strip()
            for code in (row.get("codes") or []):
                code = str(code)
                by_code_songs.setdefault(code, set()).add(song)
                if clause:
                    by_code_clause.setdefault(code, Counter())[clause] += 1

        out = []
        for code, songs in by_code_songs.items():
            if len(songs) < min_songs:
                continue
            clauses = by_code_clause.get(code) or Counter()
            out.append({
                "code": code,
                "songs": len(songs),
                "example_clause": clauses.most_common(1)[0][0] if clauses else "",
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
