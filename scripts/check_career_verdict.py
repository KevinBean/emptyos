#!/usr/bin/env python3
"""check_career_verdict — has this company/person already been dealt with?

On-demand pre-check for any agent (Claude, Codex, human) about to research,
evaluate, or write to a company or recruiter. Greps the durable career records
in the vault and reports whether prior contact, a prior verdict, or a *live
parallel thread* already exists — so work is never re-derived against a question
that has already been answered, and two sessions never research the same target
cold.

    python scripts/check_career_verdict.py "<company or person>" [--json] [--days N] [--wide]
    python scripts/check_career_verdict.py "Power Control Engineers"
    python scripts/check_career_verdict.py Fyfe --json
    python scripts/check_career_verdict.py "Chelsea Baigent"

Exit code IS the signal (agent-cli convention, `.claude/rules/agent-cli.md`):
    0  no prior record  → fresh research is OK  (also: no vault configured)
    1  prior record exists → STOP, read it, don't re-derive
    2  bad usage

Why this exists
---------------
The repo domain has had a gate since 2026-07 (`check_borrow_verdict.py`), and
CLAUDE.md makes it mandatory before evaluating an external repo. The career
domain had no equivalent: `Job-Application-Tracker.md` is a *write* endpoint in
`life-job-evaluator`, never a read-first check. On 2026-08-27 a session
researched two companies cold that already had live recruiter threads logged
(tracker rows 35 and 38, one opened that same morning by a parallel session),
and re-derived an employer comparison that already existed as a dated note.
The lesson was already in memory — `feedback_grep_vault_projects_before_planning`,
recorded after it cost an 880-line duplicate — and passive recall did not fire
it. A memory that fails to prevent its own recurrence is evidence about the
mechanism, not the content. Hence a gate.

Pure stdlib. No kernel import, no daemon, no network.
"""
from __future__ import annotations

import json
import re
import sys
import tomllib
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Scanned in full — the durable career record.
CAREER_DIR = "20_Areas/Career"
# Scanned shallowly (depth <= 2) — project notes that may carry a prior verdict.
PROJECT_DIR = "10_Projects"

TEXT_SUFFIXES = {".md", ".json", ".txt"}
MAX_BYTES = 2_000_000
DEFAULT_RECENT_DAYS = 7

# Dates as they actually appear in the tracker + briefs.
_DATE_PATTERNS = [
    re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b"),                       # 2026-08-27
    re.compile(r"\b([A-Z][a-z]{2})\s+(\d{1,2}),?\s+(20\d{2})\b"),        # Aug 21, 2026
]
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}

_STOPWORDS = {
    "pty", "ltd", "limited", "inc", "llc", "group", "australia", "au",
    "the", "and", "of", "engineering", "engineers", "energy", "power",
}


def memory_dir() -> Path | None:
    """Claude Code's per-project memory dir for THIS repo, or None.

    `D:\\emptyos` → `~/.claude/projects/D--emptyos/memory`. Derived, never
    hardcoded — the corridor itself is personal data and must not enter the
    repo (CLAUDE.md rule 13). This script READS the memory rather than copying
    it, which is also the point: the failure being fixed is a lossy MEMORY.md
    one-liner suppressing the full file read.
    """
    slug = str(REPO_ROOT).replace(":", "-").replace("\\", "-").replace("/", "-")
    d = Path.home() / ".claude" / "projects" / slug / "memory"
    return d if d.is_dir() else None


def geo_memory() -> tuple[Path, str] | None:
    """The geographic-constraint memory, if one exists."""
    d = memory_dir()
    if d is None:
        return None
    for p in sorted(d.glob("*.md")):
        stem = p.stem.lower()
        if "geograph" in stem or "location" in stem or "_based_in_" in stem:
            try:
                return p, p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return None


def _excluded_places(text: str) -> list[str]:
    """Place names under an 'out of scope' / 'explicitly out' section."""
    places: list[str] = []
    capturing = False
    for line in text.splitlines():
        low = line.lower()
        if re.search(r"(out of scope|explicitly out|not acceptable)", low):
            capturing = True
            continue
        if capturing and line.startswith("#"):
            break
        if capturing and low.strip().startswith(("- ", "* ")):
            # A bullet is a comma/dash/"and"-separated run of place names, often
            # with a heading before the dash and a parenthetical aside:
            #   "**Western Sydney** — Seven Hills, Huntingwood, Parramatta and similar."
            #   "Adelaide (regardless of company quality or salary)"
            body = re.sub(r"\*\*|`", "", line.strip()[2:])
            body = re.split(r"\(", body)[0]
            for chunk in re.split(r",|\s+[—–-]\s+|\s+and\s+", body):
                name = chunk.strip().rstrip(".")
                # A place name is capitalised and short; "similar", "any other
                # off-corridor suburb" and prose tails are neither.
                if 3 <= len(name) <= 40 and name[:1].isupper() \
                        and not name.lower().startswith("any "):
                    places.append(name)
    seen, out = set(), []
    for p in places:
        if p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return out


def _corridor_line(text: str) -> str:
    """The 'the acceptable corridor is …' sentence, whitespace-normalised.

    Spans newlines on purpose — markdown wraps, and a line-based read silently
    truncated the allowlist to its first two suburbs.
    """
    m = re.search(r"acceptable corridor is(.*?)(?:\n\s*\n|\.\s|\.$)", text,
                  re.I | re.S)
    if not m:
        return ""
    return "The acceptable corridor is" + " ".join(m.group(1).split())


def _corridor_places(line: str) -> list[str]:
    """Allowed place names from the 'the acceptable corridor is X' sentence."""
    if not line:
        return []
    tail = re.split(r"acceptable corridor is", line, flags=re.I)[-1]
    tail = re.sub(r"\*\*|`", "", tail)
    tail = re.split(r"\(", tail)[0]
    tail = re.sub(r"^[^:]{0,40}:", "", tail)          # drop "North Shore + CBD:"
    out = []
    for chunk in re.split(r",|\s+and\s+|\+", tail):
        # "and the CBD" → "CBD"; without this the article makes the chunk
        # lowercase and the capitalisation filter drops a real corridor entry.
        name = re.sub(r"^the\s+", "", chunk.strip(), flags=re.I).rstrip(".")
        if 2 <= len(name) <= 30 and name[:1].isupper():
            out.append(name)
    return out


def check_location(loc: str) -> dict:
    """Test a location against the geographic memory. Never guesses silently.

    The memory is an ALLOWLIST ("the acceptable corridor is …") with a denylist
    beside it. Checking only the denylist cannot be complete — `Newcastle` is
    not named there and slipped through on the first live run. So a location
    matching neither is reported `off_corridor` and fails, not passes: the file
    itself says "Sydney is not granular enough", which makes failing an
    ungranular location the correct behaviour rather than a false positive.
    """
    got = geo_memory()
    if got is None:
        return {"status": "no_memory", "hits": [], "file": None,
                "places": [], "corridor": "", "corridor_places": []}
    path, text = got
    places = _excluded_places(text)
    corridor = _corridor_line(text)
    allowed = _corridor_places(corridor)
    low = loc.lower()

    hits = [p for p in places if re.search(rf"\b{re.escape(p.lower())}\b", low)]
    on = [a for a in allowed if re.search(rf"\b{re.escape(a.lower())}\b", low)]
    if hits:
        status = "excluded"
    elif on or "remote" in low:
        status = "on_corridor"
    else:
        status = "off_corridor"
    return {"status": status, "hits": hits, "file": path.name, "places": places,
            "corridor": corridor, "corridor_places": allowed, "on": on}


def vault_root() -> Path | None:
    """Read notes.path from emptyos.toml. None when unset or missing."""
    cfg = REPO_ROOT / "emptyos.toml"
    if not cfg.exists():
        return None
    try:
        data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    except Exception:
        return None
    raw = (data.get("notes") or {}).get("path") or ""
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_dir() else None


def candidate_terms(arg: str) -> list[str]:
    """Company/person → lowercase substrings worth searching for.

    Yields the full phrase, a domain stem when one is given, and an acronym
    when the name is >= 3 significant words (the tracker uses both
    "Power Control Engineers" and "PCE"). Individual common words are NOT
    emitted — "power" or "engineers" alone match nearly every career note.
    """
    raw = arg.strip().rstrip("/")
    core = re.sub(r"^https?://", "", raw, flags=re.I)
    core = re.sub(r"^www\.", "", core, flags=re.I)
    terms: set[str] = set()

    phrase = core.split("/")[0].strip()
    phrase_words = [w for w in re.split(r"[^A-Za-z0-9]+", phrase) if w]
    # A query made only of stopwords ("Energy", "Power Group") is not a company
    # name — it would match nearly every career note. Emit nothing so main()
    # refuses it rather than returning a wall of noise.
    all_stop = bool(phrase_words) and all(w.lower() in _STOPWORDS for w in phrase_words)
    if len(phrase) >= 3 and not all_stop:
        terms.add(phrase.lower())

    # Domain given (pceng.com.au) → stem "pceng".
    if "." in phrase and " " not in phrase:
        stem = phrase.split(".")[0]
        if len(stem) >= 3:
            terms.add(stem.lower())

    words = [w for w in re.split(r"[^A-Za-z0-9]+", phrase) if w]
    if len(words) >= 3:
        acronym = "".join(w[0] for w in words).lower()
        if len(acronym) >= 3:
            terms.add(acronym)
    # A one-word name (Acme, Globex, Initech) is itself the term. This must key
    # off the ORIGINAL word count, not off stopword filtering: "Power Control
    # Engineers" leaves only "Control" once stopwords are dropped, and matching
    # that as a substring hits nearly every career note (measured: 334 false
    # positives on the first run).
    if len(words) == 1 and words[0].lower() not in _STOPWORDS and len(words[0]) >= 4:
        terms.add(words[0].lower())

    return sorted(t for t in terms if len(t) >= 3)


def _iter_files(root: Path, wide: bool):
    career = root / CAREER_DIR
    if career.is_dir():
        for p in career.rglob("*"):
            if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES:
                yield p
    projects = root / PROJECT_DIR
    if projects.is_dir():
        pattern = "**/*" if wide else "*/*"
        for p in projects.glob(pattern):
            if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES:
                yield p


def _dates_in(text: str) -> list[date]:
    out: list[date] = []
    for pat in _DATE_PATTERNS:
        for m in pat.finditer(text):
            try:
                if m.re is _DATE_PATTERNS[0]:
                    out.append(date(int(m[1]), int(m[2]), int(m[3])))
                else:
                    mon = _MONTHS.get(m[1])
                    if mon:
                        out.append(date(int(m[3]), mon, int(m[2])))
            except ValueError:
                continue
    return out


def scan(root: Path, terms: list[str], *, wide: bool, recent_days: int) -> list[dict]:
    """Return matches: {file, line, text, term, latest_date, recent}."""
    matches: list[dict] = []
    cutoff = date.today() - timedelta(days=recent_days)
    # Acronyms need a word boundary; longer phrases are safe as substrings.
    compiled = [
        (t, re.compile(rf"\b{re.escape(t)}\b", re.I) if len(t) <= 5 else None)
        for t in terms
    ]
    for path in _iter_files(root, wide):
        try:
            if path.stat().st_size > MAX_BYTES:
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            rel = path.as_posix()
        for n, line in enumerate(content.splitlines(), 1):
            low = line.lower()
            hit = None
            for term, word_re in compiled:
                if word_re is not None:
                    if word_re.search(line):
                        hit = term
                        break
                elif term in low:
                    hit = term
                    break
            if not hit:
                continue
            found = _dates_in(line)
            latest = max(found) if found else None
            matches.append({
                "file": rel,
                "line": n,
                "text": line.strip()[:400],
                "term": hit,
                "latest_date": latest.isoformat() if latest else None,
                "recent": bool(latest and latest >= cutoff),
            })
    return matches


def _emit(ok: bool, code: str, message: str, data=None, *, as_json: bool, exit_code: int) -> None:
    if as_json:
        print(json.dumps({"ok": ok, "code": code, "message": message, "data": data}))
    else:
        print(message)
        rows = (data or {}).get("matches", [])
        recent = [m for m in rows if m["recent"]]
        if recent:
            print("\n  ⚠ RECENT — a parallel session may be on this right now:")
            for m in recent:
                print(f"    {m['file']}:{m['line']}  [{m['latest_date']}]")
                print(f"      {m['text'][:160]}")
        rest = [m for m in rows if not m["recent"]]
        if rest:
            print(f"\n  Prior records ({len(rest)}):")
            by_file: dict[str, list[dict]] = {}
            for m in rest:
                by_file.setdefault(m["file"], []).append(m)
            for f, group in sorted(by_file.items()):
                lines = ", ".join(str(g["line"]) for g in group[:8])
                more = "" if len(group) <= 8 else f" (+{len(group) - 8} more)"
                print(f"    {f}  lines {lines}{more}")
    sys.exit(exit_code)


def main(argv: list[str]) -> None:
    as_json = "--json" in argv
    wide = "--wide" in argv
    recent_days = DEFAULT_RECENT_DAYS
    args: list[str] = []
    location = ""
    it = iter([a for a in argv if a not in ("--json", "--wide")])
    for a in it:
        if a == "--days":
            try:
                recent_days = int(next(it))
            except (StopIteration, ValueError):
                _emit(False, "invalid_args", "--days needs an integer",
                      as_json=as_json, exit_code=2)
        elif a == "--location":
            location = next(it, "")
        else:
            args.append(a)

    # Geography is a HARD filter and it is checked first, because it can
    # disqualify a target before any research is worth doing. On 2026-08-27 a
    # session researched a Seven Hills role at length — a suburb named
    # explicitly in the exclusion memory — because the MEMORY.md one-liner
    # ("hard geo constraint, remote-from-Sydney OK") had compressed the
    # corridor away, and reading complete, suppressed the file read.
    if location:
        geo = check_location(location)
        if geo["status"] == "excluded":
            _emit(False, "location_excluded",
                  f"LOCATION EXCLUDED: {location!r} matches {', '.join(geo['hits'])} "
                  f"in {geo['file']} — do not research or pitch this; "
                  f"salary does not override the corridor",
                  {"location": geo, "matches": []}, as_json=as_json, exit_code=1)
        if geo["status"] == "off_corridor":
            _emit(False, "location_off_corridor",
                  f"LOCATION NOT ON CORRIDOR: {location!r} matches no allowed place "
                  f"({', '.join(geo['corridor_places']) or 'none parsed'}) in {geo['file']} — "
                  f"confirm before researching; the memory says the city name alone "
                  f"is not granular enough",
                  {"location": geo, "matches": []}, as_json=as_json, exit_code=1)
        if geo["status"] == "no_memory" and not as_json:
            print("note: no geographic-constraint memory found — location unchecked\n")
        elif geo["status"] == "on_corridor" and not as_json:
            print(f"location {location!r}: on corridor ({', '.join(geo['on']) or 'remote'}).\n")

    if len(args) != 1 or args[0].startswith("-"):
        _emit(False, "invalid_args",
              'usage: check_career_verdict.py "<company or person>" [--json] [--days N] [--wide]',
              as_json=as_json, exit_code=2)

    terms = candidate_terms(args[0])
    if not terms:
        _emit(False, "invalid_args", f"no searchable term in {args[0]!r}",
              as_json=as_json, exit_code=2)

    root = vault_root()
    if root is None:
        # A gate that breaks every fresh clone gets disabled. Skip, don't fail.
        _emit(True, "no_vault",
              "no vault configured (notes.path unset or missing) — skipping career check",
              {"matches": [], "terms": terms}, as_json=as_json, exit_code=0)

    matches = scan(root, terms, wide=wide, recent_days=recent_days)
    recent = [m for m in matches if m["recent"]]

    if matches:
        head = f"PRIOR RECORD EXISTS for {args[0]!r} — {len(matches)} line(s)"
        if recent:
            head += f", {len(recent)} within {recent_days} days"
        head += " — read it before researching or writing"
        _emit(False, "record_exists", head,
              {"matches": matches, "terms": terms, "recent_days": recent_days},
              as_json=as_json, exit_code=1)
    else:
        _emit(True, "clear",
              f"no prior career record for {args[0]!r} (searched {', '.join(terms)}) — fresh research OK",
              {"matches": [], "terms": terms}, as_json=as_json, exit_code=0)


if __name__ == "__main__":
    main(sys.argv[1:])
