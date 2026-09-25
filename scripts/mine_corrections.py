#!/usr/bin/env python3
r"""mine_corrections — find the things you keep telling the agent, from the
agent's own transcripts.

EmptyOS already turns a correction into something durable: a ``feedback``
auto-memory, a ``.claude/rules/*.md`` entry, or a hook (``guard_git_safety.py``
exists because of two repeated mistakes). What it has never had is the
*detection* half. Noticing "you have said this before" was model judgment made
mid-conversation, so a correction that was not recognised as a repeat never
became durable, and the third occurrence looked exactly like the first. The
feedback memories on disk are the ones that happened to be noticed; nobody knew
the denominator.

This is the miner shape (``.claude/rules/self-audit-loops.md``) pointed at the
one corpus it was never pointed at. trace-miner normalizes recurring *syslog
errors* into a signature, scores them by frequency x recency, and proposes into
a human-reviewed queue. Same pipeline here, with user corrections as the input::

    transcripts -> human turns -> correction filter -> term clusters
                -> lifetime count + recency score -> report

It PROPOSES and never writes. An accepted candidate becomes a memory or a rule
by the ordinary hand-authored path — ``--accept`` only records the decision so
the theme stops resurfacing, and prints a stub to fill in. Dismissals are
equally durable: a cluster judged noise stays dismissed. That is the "you review
the evidence, you decide" half, which EmptyOS has everywhere else and should not
lose here.

FALSE-POSITIVE DISCIPLINE (.claude/rules/audits.md)
---------------------------------------------------
Calibrated against this machine's 1,804 real human turns before any threshold
was fixed. Four noise classes were measured and cut in the FILTER, not left for
the reader:

  * **Most "corrections" are decisions about the domain, not about the agent.**
    The biggest one, and the reason a marker list alone is useless here: 72
    marker hits split 34 / 38, and the 38 were career decisions, image-style
    preferences, and pick-the-other-option turns. "no, let us go with X" must
    never become a standing rule. See ``AGENT_REFERENCE``.
  * **Long turns are never corrections.** Median human turn is 41 chars. With no
    length cap, ``\bdon't\b`` matched 131 turns — dominated by pasted emails,
    task briefs, and context-continuation summaries that merely *contain* the
    word. A 400-char cap takes the whole marker set to 74 hits (4.1%), and the
    survivors read as genuine corrections.
  * **Machine-authored turns wear the user's role.** Stop-hook feedback,
    ``<command-name>`` expansions, and "This session is being continued…"
    summaries are ``type: user`` records that no human typed.
  * **Bare "again" and bare "always" are requests, not corrections.**
    "plan again?" and "we can always claim the refund" both matched an early
    pattern set. Only the correction *frames* survive ("as I said", "I already
    told you", "from now on").

Clustering carries the same discipline. A term earns a theme by **lift** — how
much more it concentrates in corrections than in ordinary speech — which is what
tells ``browser`` (a real recurring instruction) apart from ``change`` (a verb
every request contains). See ``MIN_TERM_LIFT``.

Advisory, never gating. A cluster is a question for a human, and per audits.md
an ambiguous signal must never gate.

TRANSCRIPTS ROTATE. ``~/.claude/projects/**/*.jsonl`` holds roughly a month
(``feedback_session_logs_are_not_evidence_of_a_past_period``), so lifetime
counts accumulate in state across runs while the *quotes* are always re-derived
live. A cluster older than the window keeps its count and loses its evidence —
which is honest, and is why running this occasionally beats running it once.

State stores counts, statuses and turn-id hashes — never the turn text. The
quotes exist only in the report, so this leaves no second copy of transcript
content on disk (cf. the deferred agent-transcript leak scan, which walks the
same tree).

Pure file I/O + stdlib. Does NOT import emptyos.kernel — safe while the daemon
is up (.claude/rules/daemon-handling.md).

Usage::

    python scripts/mine_corrections.py                    # report open candidates
    python scripts/mine_corrections.py --all              # include resolved ones
    python scripts/mine_corrections.py --json             # agent-cli envelope
    python scripts/mine_corrections.py --dismiss 3f2a --note "one-off, not a rule"
    python scripts/mine_corrections.py --accept 3f2a      # prints a memory stub
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scanner_lib import emit_json  # noqa: E402
from emptyos.sdk.miner_state import (  # noqa: E402
    load_state,
    recency_score,
    save_state,
    sig_hash,
)

STATE_REL = "data/correction-mining/state.json"

# A correction is short. Measured: median human turn 41 chars; every marker's
# precision collapses past ~400 because pasted content starts matching.
MAX_CORRECTION_CHARS = 400

# Turns wearing the user role that no human typed.
MACHINE_PREFIXES = (
    "stop hook feedback:",
    "this session is being continued",
    "caveat: the messages below were generated",
    "[request interrupted",
    "api error",
    "<command-name>",
    "<command-message>",
    "<local-command",
    "[eos session brief]",
)

# promptSource values that mean "not a person typing".
MACHINE_PROMPT_SOURCES = frozenset({"sdk", "system"})

# Each entry is (name, pattern). Names appear in the report so a reader can see
# WHY a turn was picked up, and can argue with the specific rule rather than
# with the whole scanner.
MARKERS: tuple[tuple[str, str], ...] = (
    # Opening negation is the highest-precision signal by a wide margin: a turn
    # that STARTS with "no," is nearly always overriding what just happened.
    ("opening-no", r"^\s*(?:no+|nope|nah)\b\s*[,.!;: '\-]"),
    ("prohibition", r"\b(?:don'?t|do not|never|stop)\s+\w+"),
    # "again" and "always" only inside a correction frame — bare forms are
    # ordinary requests and were measured as noise.
    (
        "repeated",
        r"\b(?:as i (?:said|told you)|i (?:already |just )?(?:told|said|asked)|"
        r"i've (?:told|said|asked)|you (?:did|do) (?:it|this|that) again|"
        r"same (?:thing|mistake) again)\b",
    ),
    (
        "standing-rule",
        r"\b(?:from now on|going forward|in (?:the )?future|every time|each time)\b",
    ),
    (
        "judgement",
        r"\b(?:that'?s |this is |you are |you'?re )?(?:wrong|incorrect|not right|not correct)\b",
    ),
    ("hindsight", r"\bshould(?:n'?t| not)?\s+have\b"),
    # A complaint about what the agent did carries no negation at all — "you
    # ignored my suggestion" was a real correction the first marker set missed
    # entirely, because nothing in it looks like a refusal.
    ("ignored", r"\byou (?:ignored|forgot|missed|skipped|keep|kept|still)\b"),
    ("zh-correction", r"(?:不要|别再|不对|错了|说过|每次都)"),
)
_MARKER_RES = tuple((name, re.compile(pat, re.I)) for name, pat in MARKERS)

# The second filter, and the one that decides whether this scanner is useful.
# A marker alone catches every "no, let us go with X instead" — a *choice about
# the domain*, not a correction of the agent. Measured on this machine: 72
# marker hits split 34 / 38, and the 38 were career decisions, image-style
# preferences, and pick-the-other-option turns. None of them should ever become
# a standing rule; all of them would have buried the ones that should.
#
# A behaviour correction is about the agent, so it either names the agent
# ("you ignored my suggestion", "no, why you invent new thing") or prohibits an
# action outright ("don't run Python through the console"). Markers that are
# inherently about the agent's conduct — a standing rule, a "I already told
# you", a hindsight complaint — pass on their own.
#
# The third clause closes a measured recall gap: "no, use the browser
# extension, not python in the console" is a method correction with no second
# person in it at all. An imperative landing *immediately* after the negation is
# addressed to the agent — which is what keeps "no, let us go with Renewables
# design" out, since the word after its comma is "let", not a command.
AGENT_REFERENCE = re.compile(
    r"\byou(?:r|'?re|'?ve|'?ll)?\b"
    r"|^\s*(?:please\s+)?(?:don'?t|do not|never|stop)\b"
    r"|^\s*(?:no+|nope|nah)\b[,.!;: '\-]+\s*(?:please\s+)?"
    r"(?:use|run|do|make|write|check|read|call|open|put|keep|go|try|add|remove|fix|"
    r"start|build|create|delete|edit|update|search|ask|show|give|send|leave)\b",
    re.I,
)
SELF_EVIDENT_MARKERS = frozenset(
    {"repeated", "standing-rule", "hindsight", "zh-correction", "ignored"}
)

# Terms that carry no theme. Deliberately short: an over-eager stoplist hides
# real clusters, and the document-frequency ceiling below catches generic words
# this list misses without anyone having to predict them.
STOPWORDS = frozenset(
    """
a an the and or but if then than that this these those there here it its it's
is are was were be been being do does did doing done don't dont not no nope nah
you your yours i me my mine we our ours us they them their he she his her
for to of in on at by with from into onto out up down over under again more
most some any all each every both few other another same such own so very
can could should would shall will won't wont just now still yet also too only
what which who whom whose when where why how whether
have has had having get got getting go goes going went make makes made making
use used using want wants wanted need needs needed let lets like likes liked
say says said tell tells told ask asks asked think thinks thought know knows
please thanks thank ok okay yes yeah sure right wrong stop never always
comes understand sorry curious mean means meant
""".split()
) | frozenset(
    # Contraction stems. The word tokenizer splits on the apostrophe, so
    # "don't" becomes the token "don" — which is in no stoplist anyone would
    # think to write, appears in a third of all corrections, and produced the
    # top-scoring cluster on the very first run. Every auxiliary needs its stem
    # here, not just the one that got caught.
    """
don dont doesn didn wasn weren aren isn couldn shouldn wouldn haven hasn hadn
won can cannot ain let ll ve re nt

one two three first last next new old good bad better best
about after before because between during through while
thing things something anything nothing everything way ways time times
""".split()
)

# A term appearing in more than this share of corrections describes the corpus,
# not a theme. Started at 0.30 and that was too loose: with a few dozen
# corrections it admitted "build", "change", "check", "based" — verbs that
# describe *asking for work*, which is what every turn here is doing.
MAX_TERM_DOC_FRACTION = 0.15
# Terms per correction, rarest first. Caps how many clusters one turn can seed.
MAX_TERMS_PER_CORRECTION = 4

# A term earns a cluster by being *more concentrated in corrections than in
# ordinary speech* — lift = p(term | correction) / p(term | any turn). Generic
# vocabulary sits near 1 whatever the corpus size, so this is the one threshold
# here that does not drift as the transcript window grows.
#
# Measured on the first real sweep, all three at 3/37 corrections:
#     browser  global  11/1700  ->  lift 12.5   (a real theme: "use the browser
#                                                extension, not python-in-console",
#                                                said in three separate sessions)
#     <employer> global 31/1700 ->  lift  4.5   (a name that comes up constantly —
#                                                a topic, never a rule)
#     change   global  37/1700  ->  lift  3.7   (a verb every request contains)
# Three data points is thin evidence for a threshold; 5.0 sits in the measured
# gap and is exposed as --min-lift so it can be argued with from the command
# line rather than by editing this file.
MIN_TERM_LIFT = 5.0

# State retention. Every sweep files a cluster for each term shared by two
# corrections, so most entries are terms that will never recur — without a
# ceiling the file grows for the life of the machine. A year of silence at one
# or two corrections is not a pattern, so those are forgotten; a verdict or a
# cluster at threshold is never forgotten. See ``prunable``.
PRUNE_IDLE_DAYS = 365.0
PRUNE_MAX_COUNT = 2

_WORD = re.compile(r"[a-z][a-z0-9_\-./]{2,}", re.I)
_CJK = re.compile(r"[一-鿿]{2,}")
_SYSTEM_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)


# ---------------------------------------------------------------- extraction


@dataclass
class Turn:
    """One thing a human typed."""

    uid: str
    text: str
    ts: str
    session: str
    project: str
    path: str


@dataclass
class Correction:
    turn: Turn
    markers: tuple[str, ...]
    terms: tuple[str, ...] = ()


@dataclass
class Cluster:
    cid: str
    term: str
    count: int = 0
    sessions: list[str] = field(default_factory=list)
    first_seen: str = ""
    last_seen: str = ""
    status: str = "open"
    note: str = ""
    score: int = 0
    fresh: int = 0
    quotes: list[dict] = field(default_factory=list)
    # Other terms naming this same theme; filled by ``candidates``.
    aliases: list[str] = field(default_factory=list)


def default_roots() -> list[Path]:
    """Every agent transcript tree we know how to read. A missing one is simply
    absent — a machine without codex installed is not an error."""
    home = Path.home()
    return [p for p in (home / ".claude" / "projects",) if p.exists()]


def iter_transcripts(roots: list[Path]) -> list[Path]:
    """Recursive on purpose: Claude keeps one flat dir per project, but codex
    and copilot nest deeper, and this walker is the piece a second transcript
    consumer would reuse."""
    out: list[Path] = []
    for root in roots:
        out.extend(sorted(root.rglob("*.jsonl")))
    return out


def _clean(text: str) -> str:
    return _SYSTEM_REMINDER.sub("", text).strip()


def is_machine_turn(text: str, prompt_source: str | None) -> bool:
    if prompt_source in MACHINE_PROMPT_SOURCES:
        return True
    return text.lstrip().lower().startswith(MACHINE_PREFIXES)


def human_turns(path: Path) -> list[Turn]:
    """Human-typed turns from one transcript. Anything unreadable is skipped —
    a truncated line mid-write must never abort a sweep."""
    turns: list[Turn] = []
    project = path.parent.name
    try:
        fh = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return turns
    with fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("type") != "user" or rec.get("isSidechain"):
                continue
            msg = rec.get("message") or {}
            content = msg.get("content")
            # A list content block is a tool_result — machinery, not speech.
            if not isinstance(content, str):
                continue
            if is_machine_turn(content, rec.get("promptSource")):
                continue
            text = _clean(content)
            if not text:
                continue
            uid = rec.get("uuid") or hashlib.sha1(
                f"{path.name}{rec.get('timestamp')}{text[:80]}".encode("utf-8", "replace")
            ).hexdigest()
            turns.append(
                Turn(
                    uid=str(uid),
                    text=text,
                    ts=str(rec.get("timestamp") or ""),
                    session=str(rec.get("sessionId") or path.stem),
                    project=project,
                    path=str(path).replace("\\", "/"),
                )
            )
    return turns


def match_markers(text: str) -> tuple[str, ...]:
    """Which correction markers fire on this text (empty tuple = not one)."""
    if len(text) > MAX_CORRECTION_CHARS:
        return ()
    return tuple(name for name, rx in _MARKER_RES if rx.search(text))


def is_behaviour_correction(text: str, markers: tuple[str, ...]) -> bool:
    """Is this a correction of the AGENT, or a decision about the domain?

    Only the first kind can become a standing rule. See ``AGENT_REFERENCE``.
    """
    if not markers:
        return False
    if any(m in SELF_EVIDENT_MARKERS for m in markers):
        return True
    return bool(AGENT_REFERENCE.search(text))


def salient_terms(text: str) -> list[str]:
    """Theme-bearing tokens, in order. Latin words plus CJK bigrams."""
    seen: list[str] = []
    for m in _WORD.finditer(text):
        w = m.group(0).lower().strip("-./")
        if len(w) < 3 or w in STOPWORDS or w.isdigit():
            continue
        if w not in seen:
            seen.append(w)
    for run in _CJK.findall(text):
        for i in range(len(run) - 1):
            bg = run[i : i + 2]
            if bg not in seen:
                seen.append(bg)
    return seen


def term_lift(corr_df: int, n_corr: int, global_df: int, n_turns: int) -> float:
    """How much more this term concentrates in corrections than in speech."""
    if not corr_df or not n_corr or not n_turns:
        return 0.0
    base = max(global_df, corr_df) / n_turns
    return (corr_df / n_corr) / base if base else 0.0


def assign_terms(
    corrections: list[Correction],
    all_turns: list[Turn],
    *,
    min_lift: float = MIN_TERM_LIFT,
) -> None:
    """Give each correction the most correction-specific terms it carries.

    Three filters, each cutting a different way a term can be uninteresting:

    * **Shared** (correction df >= 2) — a term only one correction uses can never
      be a recurring theme.
    * **Not the corpus itself** (``MAX_TERM_DOC_FRACTION``) — a term in a third
      of all corrections describes the corpus.
    * **Concentrated in corrections** (``MIN_TERM_LIFT``, measured against the
      *whole* human-turn corpus) — this is the one that separates a theme from a
      verb, and it needs the un-filtered turns to compute, which is why they are
      a parameter rather than something derived from ``corrections``.

    A correction may seed several clusters: the same evidence legitimately
    supports more than one candidate theme, and the human reads the quotes to
    decide which. Counts dedupe by turn id WITHIN a cluster, so nothing
    double-counts itself.
    """
    df: Counter[str] = Counter()
    for c in corrections:
        for t in set(salient_terms(c.turn.text)):
            df[t] += 1
    gdf: Counter[str] = Counter()
    for t in all_turns:
        for w in set(salient_terms(t.text)):
            gdf[w] += 1

    n_corr, n_turns = len(corrections), len(all_turns)
    # The absolute floor matters more than the fraction. A share-based ceiling
    # inverts on a small corpus: with 3 corrections, 15% rounds to 0, so the one
    # term all three share — the theme — is thrown out as "describes the corpus".
    # Below ~8 corrections nothing can be corpus-describing yet.
    ceiling = max(8, int(n_corr * MAX_TERM_DOC_FRACTION))
    lift = {
        t: term_lift(df[t], n_corr, gdf[t], n_turns) for t in df if 2 <= df[t] <= ceiling
    }
    for c in corrections:
        cand = [t for t in salient_terms(c.turn.text) if lift.get(t, 0.0) >= min_lift]
        # Highest lift first: the most correction-specific term is the one whose
        # cluster a reader can actually name.
        cand.sort(key=lambda t: (-lift[t], df[t], t))
        c.terms = tuple(cand[:MAX_TERMS_PER_CORRECTION])


# ------------------------------------------------------------------- scoring


def _days_between(a: str, b: str) -> float:
    try:
        da = datetime.fromisoformat(a.replace("Z", "+00:00"))
        db = datetime.fromisoformat(b.replace("Z", "+00:00"))
    except Exception:
        return 0.0
    return abs((db - da).total_seconds()) / 86400.0


def build_clusters(
    corrections: list[Correction],
    state: dict,
    *,
    now: str,
    recent_days: float = 14.0,
) -> tuple[dict[str, Cluster], dict[str, set[str]]]:
    """Merge this sweep's corrections into the persisted findings dict.

    Lifetime ``count`` accumulates across runs (transcripts rotate out from
    under us); the returned seen-sets hold turn-id hashes so re-running over the
    same window cannot inflate anything.
    """
    stored = dict(state.get("clusters") or {})
    seen_sets: dict[str, set[str]] = {
        cid: set(rec.get("seen") or []) for cid, rec in stored.items()
    }

    by_term: dict[str, list[Correction]] = defaultdict(list)
    for c in corrections:
        for t in c.terms:
            by_term[t].append(c)

    out: dict[str, Cluster] = {}
    # Carry forward every stored cluster, even one whose evidence has rotated
    # away — its count is the whole point of persisting.
    for cid, rec in stored.items():
        out[cid] = Cluster(
            cid=cid,
            term=str(rec.get("term") or ""),
            count=int(rec.get("count") or 0),
            sessions=list(rec.get("sessions") or []),
            first_seen=str(rec.get("first_seen") or ""),
            last_seen=str(rec.get("last_seen") or ""),
            status=str(rec.get("status") or "open"),
            note=str(rec.get("note") or ""),
        )

    for term, members in by_term.items():
        cid = sig_hash(f"correction:{term}")
        cl = out.get(cid) or Cluster(cid=cid, term=term)
        cl.term = term
        seen = seen_sets.setdefault(cid, set())
        for c in sorted(members, key=lambda x: x.turn.ts):
            if c.turn.uid in seen:
                continue
            seen.add(c.turn.uid)
            cl.count += 1
            if c.turn.session not in cl.sessions:
                cl.sessions.append(c.turn.session)
            if not cl.first_seen or (c.turn.ts and c.turn.ts < cl.first_seen):
                cl.first_seen = c.turn.ts
            if c.turn.ts > cl.last_seen:
                cl.last_seen = c.turn.ts
        cl.quotes = [
            {
                "uid": c.turn.uid,
                "ts": c.turn.ts,
                "text": c.turn.text,
                "markers": list(c.markers),
                "session": c.turn.session,
                "project": c.turn.project,
                # Where to go read what the correction was answering.
                "path": c.turn.path,
            }
            for c in sorted(members, key=lambda x: x.turn.ts, reverse=True)
        ]
        cl.fresh = sum(1 for c in members if _days_between(c.turn.ts, now) <= recent_days)
        out[cid] = cl

    for cl in out.values():
        idle = _days_between(cl.last_seen, now) if cl.last_seen else 999.0
        cl.score = recency_score(cl.count, cl.fresh, days_idle=idle)
    return out, seen_sets


def prunable(cl: Cluster, *, now: str, idle_days: float = PRUNE_IDLE_DAYS) -> bool:
    """Is this cluster safe to forget entirely?

    Only a term that (a) never reached the reporting threshold, (b) has been
    silent for a year, and (c) carries no human decision. Accepted and dismissed
    clusters are verdicts and are kept forever — re-proposing something already
    judged is the one failure this state file exists to prevent. Anything at or
    above threshold is kept too, because the accumulating count across a
    rotating transcript window IS the mechanism.
    """
    if cl.status != "open" or cl.count > PRUNE_MAX_COUNT:
        return False
    # >= because at exactly PRUNE_IDLE_DAYS the year of silence has elapsed.
    return not cl.last_seen or _days_between(cl.last_seen, now) >= idle_days


def to_state(
    clusters: dict[str, Cluster],
    seen_sets: dict[str, set[str]],
    *,
    now: str | None = None,
) -> dict:
    stored: dict[str, dict] = {}
    for cid, cl in clusters.items():
        if now and prunable(cl, now=now):
            continue
        stored[cid] = {
            "term": cl.term,
            "count": cl.count,
            "sessions": cl.sessions[-20:],
            "first_seen": cl.first_seen,
            "last_seen": cl.last_seen,
            "status": cl.status,
            "note": cl.note,
            "seen": sorted(seen_sets.get(cid, set())),
        }
    return {"version": 1, "clusters": stored}


def evidence_key(cl: Cluster) -> frozenset[str]:
    """The turn ids this cluster was built from *this sweep*.

    Empty when the evidence has rotated out of the transcript window, which is
    why every caller treats an empty key as "cannot be grouped" rather than as
    "groups with every other empty one".
    """
    return frozenset(q["uid"] for q in cl.quotes)


def alias_group(clusters: dict[str, Cluster], cid: str) -> list[Cluster]:
    """A cluster plus every other one standing on identical evidence.

    A verdict applies to the whole group: dismissing "browser" while leaving its
    alias "extension" open would resurface the same three quotes next sweep
    under a different name, which is precisely the review fatigue this scanner
    exists to remove.
    """
    target = clusters[cid]
    key = evidence_key(target)
    if not key:
        return [target]
    return [c for c in clusters.values() if evidence_key(c) == key]


def candidates(
    clusters: dict[str, Cluster],
    *,
    min_count: int,
    min_sessions: int,
    include_resolved: bool,
) -> list[Cluster]:
    """The report set.

    ``min_sessions`` is the load-bearing threshold, not ``min_count``: two
    corrections inside one conversation are one argument being had, while the
    same correction in two different sessions is the thing that should have been
    durable.
    """
    out = [
        c
        for c in clusters.values()
        if c.count >= min_count
        and len(set(c.sessions)) >= min_sessions
        and (include_resolved or c.status == "open")
    ]
    out.sort(key=lambda c: (-c.score, -c.count, c.term))

    # Collapse aliases. Terms that co-occur in every one of their corrections —
    # "browser" and "extension" in the same three turns — are one theme wearing
    # two names, and showing both means reading the same quotes twice and
    # judging them twice. The alias keeps its own cluster id (ids are
    # term-keyed and must stay stable across runs) but is reported under the
    # winner, and a verdict on either covers the group. See ``alias_group``.
    collapsed: list[Cluster] = []
    claimed: set[frozenset[str]] = set()
    for c in out:
        key = evidence_key(c)
        if key and key in claimed:
            continue
        if key:
            claimed.add(key)
            c.aliases = [o.term for o in out if o is not c and evidence_key(o) == key]
        collapsed.append(c)
    return collapsed


# -------------------------------------------------------------------- output


MEMORY_STUB = """\
---
name: <short-kebab-case-slug>
description: <one line — what to do differently>
metadata:
  type: feedback
---
<the standing instruction, in one or two sentences>

**Why:** {n} corrections across {s} sessions, {first} to {last}. {quotes}

**How to apply:** <the concrete move to make next time>
"""


def render_stub(cl: Cluster) -> str:
    quotes = " ".join(f'"{q["text"][:120]}"' for q in cl.quotes[:3])
    return MEMORY_STUB.format(
        n=cl.count,
        s=len(set(cl.sessions)),
        first=(cl.first_seen or "?")[:10],
        last=(cl.last_seen or "?")[:10],
        quotes=quotes or "(evidence has rotated out of the transcript window)",
    )


def print_report(cands: list[Cluster], *, scanned: int, corrections: int, quotes: int) -> None:
    print(f"scanned {scanned} human turns -> {corrections} corrections")
    if not cands:
        print("no repeated correction themes above threshold.")
        return
    print(f"\n{len(cands)} candidate theme(s) — evidence below, decision yours:\n")
    for cl in cands:
        flag = "" if cl.status == "open" else f"  [{cl.status}]"
        also = f" (also: {', '.join(cl.aliases)})" if cl.aliases else ""
        print(
            f"  {cl.cid[:6]}  score {cl.score:>3}  x{cl.count} across "
            f"{len(set(cl.sessions))} session(s)  \"{cl.term}\"{also}  "
            f"last {(cl.last_seen or '?')[:10]}{flag}"
        )
        if cl.note:
            print(f"          note: {cl.note}")
        for q in cl.quotes[:quotes]:
            print(f"          {(q['ts'] or '')[:10]}  {' '.join(q['text'].split())[:150]}")
        if not cl.quotes:
            print("          (evidence rotated out of the transcript window)")
        print()
    print("  --accept <id>   record it + print a memory stub")
    print("  --dismiss <id>  never surface this theme again")
    # Last line is the summary because preflight quotes a check's final line as
    # its one-line verdict — a trailing usage hint reads there as gibberish.
    n_open = len([c for c in cands if c.status == "open"])
    print(f"\n{n_open} open theme(s) may be worth a memory, a rule, or a hook.")


# ---------------------------------------------------------------------- main


def sweep(
    roots: list[Path], state: dict, *, now: str, min_lift: float = MIN_TERM_LIFT
) -> tuple[dict[str, Cluster], dict[str, set[str]], int, int]:
    turns: list[Turn] = []
    for path in iter_transcripts(roots):
        turns.extend(human_turns(path))
    corrections = [
        Correction(turn=t, markers=m)
        for t in turns
        if (m := match_markers(t.text)) and is_behaviour_correction(t.text, m)
    ]
    assign_terms(corrections, turns, min_lift=min_lift)
    clusters, seen_sets = build_clusters(corrections, state, now=now)
    return clusters, seen_sets, len(turns), len(corrections)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Mine agent transcripts for repeated user corrections."
    )
    ap.add_argument("--root", action="append", default=None, help="transcript root (repeatable)")
    ap.add_argument("--state", default=None, help=f"state file (default: {STATE_REL})")
    ap.add_argument("--min-count", type=int, default=3, help="lifetime corrections (default 3)")
    ap.add_argument("--min-sessions", type=int, default=2, help="distinct sessions (default 2)")
    ap.add_argument("--quotes", type=int, default=3, help="evidence lines per theme (default 3)")
    ap.add_argument(
        "--min-lift",
        type=float,
        default=MIN_TERM_LIFT,
        help=f"term concentration vs ordinary speech (default {MIN_TERM_LIFT})",
    )
    ap.add_argument("--all", action="store_true", help="include accepted/dismissed themes")
    ap.add_argument("--json", action="store_true", help="agent-cli envelope")
    # One verdict per invocation — a theme cannot be accepted and dismissed at
    # once, and silently letting the last flag win would hide the mistake.
    verdicts = ap.add_mutually_exclusive_group()
    verdicts.add_argument("--accept", metavar="ID", help="mark a theme accepted; prints a memory stub")
    verdicts.add_argument("--dismiss", metavar="ID", help="mark a theme dismissed (never resurfaces)")
    verdicts.add_argument("--reopen", metavar="ID", help="undo an accept/dismiss")
    ap.add_argument("--note", default="", help="reason recorded with --accept/--dismiss")
    ap.add_argument("--no-save", action="store_true", help="do not write state (dry run)")
    args = ap.parse_args(argv)

    repo = Path(__file__).resolve().parent.parent
    state_path = Path(args.state) if args.state else repo / STATE_REL
    roots = [Path(r).expanduser() for r in args.root] if args.root else default_roots()
    now = datetime.now(UTC).isoformat()

    if not roots:
        msg = "no transcript roots found (looked for ~/.claude/projects)"
        if args.json:
            return emit_json(True, "no_transcripts", msg, {"candidates": []})
        print(msg)
        return 0

    state = load_state(state_path)
    clusters, seen_sets, n_turns, n_corr = sweep(
        roots, state, now=now, min_lift=args.min_lift
    )

    verdict: Cluster | None = None
    for flag, status in (("accept", "accepted"), ("dismiss", "dismissed"), ("reopen", "open")):
        ident = getattr(args, flag)
        if not ident:
            continue
        hit = [c for c in clusters.values() if c.cid.startswith(ident)]
        if len(hit) != 1:
            msg = f"{'no' if not hit else 'ambiguous'} theme id {ident!r}"
            if args.json:
                return emit_json(False, "bad_id", msg, {"matched": [c.cid for c in hit]})
            print(msg)
            return 2
        # The verdict covers every cluster standing on the same evidence, so an
        # alias cannot resurface the quotes you just judged.
        for member in alias_group(clusters, hit[0].cid):
            member.status = status
            member.note = args.note or member.note
        verdict = hit[0]
        verdict.aliases = [
            c.term for c in alias_group(clusters, hit[0].cid) if c is not verdict
        ]

    if not args.no_save:
        save_state(state_path, to_state(clusters, seen_sets, now=now))

    if verdict is not None:
        if args.json:
            return emit_json(
                True,
                "ok",
                f"{verdict.cid[:6]} -> {verdict.status}",
                {
                    "id": verdict.cid,
                    "status": verdict.status,
                    "aliases": verdict.aliases,
                    "stub": render_stub(verdict),
                },
            )
        also = f" (+ {', '.join(verdict.aliases)})" if verdict.aliases else ""
        print(f'{verdict.cid[:6]} "{verdict.term}"{also} -> {verdict.status}')
        if verdict.status == "accepted":
            print("\nWrite it as a memory (or a rule, or a hook) — stub:\n")
            print(render_stub(verdict))
        return 0

    cands = candidates(
        clusters,
        min_count=args.min_count,
        min_sessions=args.min_sessions,
        include_resolved=args.all,
    )

    if args.json:
        return emit_json(
            not cands,
            "candidates",
            f"{len(cands)} repeated correction theme(s)",
            {
                "scanned_turns": n_turns,
                "corrections": n_corr,
                "candidates": [
                    {
                        "id": c.cid,
                        "term": c.term,
                        "count": c.count,
                        "sessions": len(set(c.sessions)),
                        "score": c.score,
                        "status": c.status,
                        "first_seen": c.first_seen,
                        "last_seen": c.last_seen,
                        "note": c.note,
                        "aliases": c.aliases,
                        "quotes": c.quotes[: args.quotes],
                    }
                    for c in cands
                ],
            },
        )

    print_report(cands, scanned=n_turns, corrections=n_corr, quotes=args.quotes)
    return len([c for c in cands if c.status == "open"])


if __name__ == "__main__":
    sys.exit(main())
