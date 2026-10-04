"""Attested Memory — derived trust over vault-note provenance (pure core).

Implements the trust function + fidelity audit from ``docs/MEMORY.md``. Pure:
no kernel, no I/O, no LLM — every function takes its inputs (a note's
frontmatter ``properties`` dict, the current ``date``) as arguments, so it
unit-tests without a daemon.

Spine (``docs/MEMORY.md`` §1): trust is **derived** from provenance facts,
never stored. These functions read indexed frontmatter and return a
*categorical, explainable* verdict — they never write, never call a model. The
``BaseApp`` wrappers (``vault_trust`` / ``vault_confirm`` / ``memory_audit``)
supply the real frontmatter + clock; everything decision-shaped lives here.

Performance keystone (invariant #7): every input here is a flat frontmatter
field already resident in ``VaultIndex`` RAM. No call reads a note body, hits
disk, or invokes an LLM.

The trust function is **versioned policy** (``TRUST_FUNCTION_VERSION``), not a
stored number — changing it shifts every verdict at once, so it stays
categorical (no magic weights) and explainable (each verdict carries the
provenance facts that drove it).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# Bump when the verdict logic changes — stamped onto every TrustVerdict so a
# downstream reader knows which policy produced a verdict.
TRUST_FUNCTION_VERSION = 1

# ── attestation axis (refines `author:`) ──
ASSERTED = "asserted"   # the user said it
INFERRED = "inferred"   # AI derived it
IMPORTED = "imported"   # copied from an external source
OBSERVED = "observed"   # the system logged an event
_ATTESTATIONS = frozenset({ASSERTED, INFERRED, IMPORTED, OBSERVED})

# ── trust levels (the derived verdict) ──
TRUSTED = "trusted"
TENTATIVE = "tentative"
SUSPECT = "suspect"
RETIRED = "retired"

# Frontmatter fields whose presence makes a claim time-sensitive: a stale
# `last_verified` on one of these is worth a re-check.
_TIME_SENSITIVE_FIELDS = ("due", "due_ts", "expires_at", "next_review")

# Default scope (invariant #5): only machine-touched memory is in play. The
# ~4k hand-written vault notes are exempt — they carry none of these markers.
DEFAULT_MEMORY_TAGS = frozenset({"aura-memory", "kb"})

# Default re-verification horizon (days) before a confirmed/time-sensitive
# claim is considered stale.
DEFAULT_STALE_DAYS = 180


@dataclass
class TrustVerdict:
    """The derived trust read for one claim. Explainable + versioned."""

    level: str                     # trusted | tentative | suspect | retired
    attestation: str               # asserted | inferred | imported | observed | ""
    reasons: list[str] = field(default_factory=list)
    age_days: int | None = None    # days since last_verified, or None
    version: int = TRUST_FUNCTION_VERSION

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "attestation": self.attestation,
            "reasons": list(self.reasons),
            "age_days": self.age_days,
            "version": self.version,
        }


@dataclass
class AuditReport:
    """Read-only fidelity audit over a scoped set of claims (``docs/MEMORY.md`` §8)."""

    dial: int                      # % of in-scope claims that read `trusted`
    total: int                     # in-scope (machine-touched) claim count
    counts: dict                   # {level: count}
    stale: list[dict]              # top-N claims needing attention (budgeted)
    version: int = TRUST_FUNCTION_VERSION

    def to_dict(self) -> dict:
        return {
            "dial": self.dial,
            "total": self.total,
            "counts": dict(self.counts),
            "stale": list(self.stale),
            "version": self.version,
        }


def _parse_date(val) -> date | None:
    """Lenient YYYY-MM-DD / ISO-datetime → date. None on anything unparseable."""
    if not val:
        return None
    s = str(val).strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def attestation_of(props: dict) -> str:
    """Derive the attestation axis for a note (``docs/MEMORY.md`` §3).

    Explicit ``attestation:`` wins; otherwise it's refined from ``author:``
    (user/both → asserted, ai → inferred). Returns "" when provenance is
    unknown — the fail-closed case (never silently trusted).
    """
    raw = str(props.get("attestation") or "").strip().lower()
    if raw in _ATTESTATIONS:
        return raw
    author = str(props.get("author") or "").strip().lower()
    if author in ("user", "both"):
        return ASSERTED
    if author == "ai":
        return INFERRED
    return ""


def is_time_sensitive(props: dict) -> bool:
    """True when a stale ``last_verified`` would matter (a committed-future field)."""
    return any(props.get(f) for f in _TIME_SENSITIVE_FIELDS)


def _normalize_tags(props: dict) -> set[str]:
    tags = props.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    return {str(t).strip().lower() for t in tags if str(t).strip()}


def is_machine_touched(props: dict, *, memory_tags=DEFAULT_MEMORY_TAGS) -> bool:
    """Scope gate (invariant #5): is this claim in the Attested-Memory domain?

    True for AI-authored/inferred notes and explicitly-curated memory corpora
    (aura-memory, KB). False for the hand-written episodic vault — a user note
    with no provenance markers is exempt and must never be annotated/audited.
    """
    if str(props.get("attestation") or "").strip().lower() in _ATTESTATIONS:
        return True
    if str(props.get("author") or "").strip().lower() in ("ai", "both"):
        return True
    return bool(_normalize_tags(props) & {t.lower() for t in memory_tags})


def classify_trust(
    props: dict, *, now: date, stale_days: int = DEFAULT_STALE_DAYS
) -> TrustVerdict:
    """Derive a categorical trust verdict from a note's provenance frontmatter.

    Order is significant (``docs/MEMORY.md`` §4):

    1. ``superseded_by`` present → **retired** (kept for audit, never deleted).
    2. asserted / imported / observed → **trusted**, unless time-sensitive AND
       its confirmation went stale → **suspect**. (A user-stated fact stays
       true until superseded; it does not require periodic re-verification.)
    3. inferred → **tentative** until confirmed; **trusted** once confirmed and
       fresh; **suspect** once its confirmation goes stale.
    4. unknown provenance → **tentative** (fail closed, never trusted).
    """
    superseded = props.get("superseded_by")
    if superseded:
        return TrustVerdict(
            RETIRED, attestation_of(props), [f"superseded_by {superseded}"]
        )

    att = attestation_of(props)
    lv = _parse_date(props.get("last_verified") or props.get("as_of"))
    age = (now - lv).days if lv else None
    stale = age is not None and age > stale_days
    tsens = is_time_sensitive(props)

    reasons = [f"attestation={att or 'unknown'}"]
    reasons.append(f"last_verified {age}d ago" if age is not None else "never verified")
    if tsens:
        reasons.append("time-sensitive")

    if att in (ASSERTED, IMPORTED, OBSERVED):
        if tsens and stale:
            return TrustVerdict(
                SUSPECT, att, reasons + ["time-sensitive + verification stale"], age
            )
        return TrustVerdict(TRUSTED, att, reasons, age)

    if att == INFERRED:
        if lv is None:
            return TrustVerdict(
                TENTATIVE, att, reasons + ["AI-inferred, unconfirmed"], age
            )
        if stale:
            return TrustVerdict(
                SUSPECT, att, reasons + ["AI-inferred, confirmation stale"], age
            )
        return TrustVerdict(TRUSTED, att, reasons + ["confirmed"], age)

    return TrustVerdict(TENTATIVE, att, reasons + ["unknown provenance"], age)


# Stale-list priority: surface what most needs a human look. SUSPECT (was
# relied on, drifted) outranks TENTATIVE (never confirmed). RETIRED/TRUSTED
# don't need attention.
_STALE_PRIORITY = {SUSPECT: 0, TENTATIVE: 1}


def fidelity_audit(
    rows: list[dict],
    *,
    now: date,
    stale_days: int = DEFAULT_STALE_DAYS,
    budget: int = 20,
    memory_tags=DEFAULT_MEMORY_TAGS,
) -> AuditReport:
    """Read-only fidelity pass over candidate notes (``docs/MEMORY.md`` §8).

    ``rows`` are VaultIndex-shaped dicts (``{"path", "properties", ...}``). Only
    machine-touched rows count (scope, invariant #5). Returns the fidelity dial
    (% trusted) plus the top-``budget`` claims needing attention. **Never
    writes** — the caller decides whether to surface these as review-gate
    proposals.
    """
    counts = {TRUSTED: 0, TENTATIVE: 0, SUSPECT: 0, RETIRED: 0}
    candidates: list[dict] = []
    total = 0

    for r in rows:
        props = r.get("properties") or {}
        if not is_machine_touched(props, memory_tags=memory_tags):
            continue
        total += 1
        v = classify_trust(props, now=now, stale_days=stale_days)
        counts[v.level] = counts.get(v.level, 0) + 1
        if v.level in _STALE_PRIORITY:
            source = props.get("source") or props.get("source_ref") or ""
            if not source:
                refs = props.get("references") or []
                if isinstance(refs, str):
                    refs = [refs]
                source = refs[0] if refs else ""
            candidates.append(
                {
                    "path": r.get("path", ""),
                    "level": v.level,
                    "attestation": v.attestation,
                    "reasons": v.reasons,
                    "age_days": v.age_days,
                    "last_verified": props.get("last_verified") or props.get("as_of") or "",
                    "source": str(source),
                }
            )

    # Most-suspect first, then oldest verification (None age sorts last).
    candidates.sort(
        key=lambda c: (
            _STALE_PRIORITY.get(c["level"], 9),
            -(c["age_days"] if c["age_days"] is not None else -1),
        )
    )

    dial = round(100 * counts[TRUSTED] / total) if total else 0
    return AuditReport(dial=dial, total=total, counts=counts, stale=candidates[:budget])
