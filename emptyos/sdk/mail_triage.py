"""Shared Gmail-triage primitives — chunked LLM classification of message
metadata into caller-defined categories, plus Gmail-importable filter XML.

Consumers: the general ``mail`` app (general inbox categories) and the ``jobs``
app's career lens (career buckets + application matching). Extracted per
CLAUDE.md Dev Rule 9 once the second consumer (the ``mail`` app) arrived.

Pure except for the injected async ``think_fn`` — no kernel, no I/O, no
BaseApp dependency. The caller owns the think capability and the category set;
this module owns the chunking, the prompt shape, the alignment/fallback, the
sender→rule grouping, and the Gmail XML serialization.

READ-ONLY by nature: nothing here mutates a mailbox. ``build_gmail_filters_xml``
only *emits* an importable file the user applies in Gmail themselves; it never
parses untrusted XML.

Metadata only: the classifier is fed sender / subject / Gmail-snippet — never
message bodies (the gmail service fetches ``format=metadata``). Rule-19 floor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from xml.sax.saxutils import escape, quoteattr

from emptyos.sdk.utils import parse_llm_json

# Max messages per classification think() call. Larger batches degrade — the
# model loses track / truncates its JSON over a long list and items fall back
# to the catch-all (observed live: 60-in-one-call → 0 hits; 20 → reliable).
CHUNK_SIZE = 20

_EMAIL_RE = re.compile(r"<([^>]+)>")


@dataclass(frozen=True)
class Category:
    """One triage bucket.

    key:         stable id the LLM emits and rows are tagged with.
    description: shown to the LLM to define the bucket.
    label:       Gmail label proposed when generating filters. "" means this
                 category is NEVER turned into a filter (the catch-all / "other").
    archive:     when True, generated filters for this category skip the inbox
                 (Gmail "shouldArchive") — for high-volume, low-urgency mail.
    min_count:   minimum recent occurrences before a sender in this category earns
                 a filter rule. 1 = every sender (the default — important categories
                 like finance/job keep singletons). Bump to 2+ for high-noise
                 categories (newsletters) to trim one-off promo senders.
    """
    key: str
    description: str
    label: str = ""
    archive: bool = False
    min_count: int = 1


def catch_all_key(categories: list[Category]) -> str:
    """The category treated as the fallback bucket — the first with no label,
    else the last category. Unknown/missing LLM rows land here."""
    for c in categories:
        if not c.label:
            return c.key
    return categories[-1].key if categories else "other"


def build_classify_system(
    categories: list[Category],
    *,
    role: str = "an email triage assistant",
    extra_fields: dict[str, str] | None = None,
) -> str:
    """Assemble the classification system prompt from a category set.

    extra_fields: optional {field_name: instruction} of extra strings to pull
    per item (e.g. {"company": "the hiring company; '' if none"} for jobs).
    """
    extra_fields = extra_fields or {}
    lines = [
        f"You are {role}. You receive a numbered list of email METADATA ONLY — "
        "sender, subject, and a short Gmail preview snippet. You never see full "
        "message bodies. Classify each item into exactly one category.",
        "",
        "Categories (choose exactly one per item, by key):",
    ]
    for c in categories:
        lines.append(f"- {c.key}: {c.description}")
    lines += [
        "",
        "Rules:",
        "- Classify ONLY from the sender / subject / snippet you are given. Do "
        "NOT invent content. When genuinely unsure, use the catch-all category "
        "with low confidence.",
        "- `confidence` is a number from 0.0 to 1.0.",
        "- `reason` is one short phrase (<= 8 words).",
    ]
    extra_keys = ""
    for field, desc in extra_fields.items():
        lines.append(f"- `{field}`: {desc}")
        extra_keys += f', "{field}": "..."'
    lines += [
        "",
        "Output ONLY a JSON array — one object per input item, in the same order, "
        "each with the input's `n`:",
        '[{"n": 1, "category": "<key>", "confidence": 0.82, "reason": "short '
        'phrase"' + extra_keys + "}]",
        "No prose, no markdown, nothing outside the JSON array.",
    ]
    return "\n".join(lines)


def _clamp_conf(v) -> float:
    try:
        return max(0.0, min(1.0, round(float(v), 2)))
    except (TypeError, ValueError):
        return 0.0


async def classify_messages(
    think_fn,
    messages: list[dict],
    categories: list[Category],
    *,
    extra_fields: dict[str, str] | None = None,
    chunk_size: int = CHUNK_SIZE,
    system: str | None = None,
) -> list[dict]:
    """Classify message metadata into ``categories``, chunked for reliability.

    think_fn(prompt: str, system: str) -> awaitable[str] — the injected think
    capability (the caller supplies provider/domain/temperature). Splits into
    batches of ``chunk_size`` and runs one think() per batch (a single large
    call degrades), then concatenates in original order. One dict per input
    message; a message the LLM skips or mislabels falls back to the catch-all
    category, never dropped. Extra fields are returned as strings.
    """
    if not messages:
        return []
    extra_fields = extra_fields or {}
    valid = {c.key for c in categories}
    fallback_key = catch_all_key(categories)
    sys_prompt = system or build_classify_system(categories, extra_fields=extra_fields)
    out: list[dict] = []
    for i in range(0, len(messages), chunk_size):
        out.extend(await _classify_chunk(
            think_fn, messages[i:i + chunk_size], valid, fallback_key,
            extra_fields, sys_prompt))
    return out


async def _classify_chunk(think_fn, messages, valid, fallback_key,
                          extra_fields, sys_prompt) -> list[dict]:
    """One think() call over a single batch (<= chunk_size) → aligned rows."""
    if not messages:
        return []
    lines = []
    for i, m in enumerate(messages, 1):
        frm = (m.get("from") or "")[:120]
        subj = (m.get("subject") or "")[:160]
        prev = (m.get("snippet") or "")[:200]
        lines.append(f"[{i}] From: {frm} | Subject: {subj} | Preview: {prev}")
    prompt = "Classify these emails:\n\n" + "\n".join(lines)

    raw = await think_fn(prompt, sys_prompt)

    # fallback=[] so a garbled LLM response degrades to all-catch-all, never raises.
    parsed = parse_llm_json(raw, fallback=[])
    if isinstance(parsed, dict):
        parsed = parsed.get("classifications") or parsed.get("results") or []
    if not isinstance(parsed, list):
        parsed = []

    by_n: dict[int, dict] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        try:
            by_n[int(item.get("n"))] = item
        except (TypeError, ValueError):
            continue

    out = []
    for i, m in enumerate(messages, 1):
        c = by_n.get(i, {})
        cat = c.get("category")
        if cat not in valid:
            cat = fallback_key
        row = {
            "id": m.get("id", ""),
            "thread_id": m.get("thread_id", ""),
            "from": m.get("from", ""),
            "subject": m.get("subject", ""),
            "date": m.get("date", ""),
            "snippet": m.get("snippet", ""),
            "category": cat,
            "confidence": _clamp_conf(c.get("confidence")),
            "reason": (c.get("reason") or "").strip(),
        }
        for field in extra_fields:
            val = c.get(field)
            row[field] = val.strip() if isinstance(val, str) else (val or "")
        out.append(row)
    return out


def extract_email(from_header: str) -> str:
    """Pull the bare address out of a ``Name <addr@host>`` From header. Returns
    the lower-cased address, or "" when none parses. Pure."""
    s = (from_header or "").strip()
    m = _EMAIL_RE.search(s)
    email = (m.group(1) if m else s).strip().lower()
    return email if "@" in email and " " not in email else ""


def apply_overrides(classified: list[dict], overrides, *,
                    catch_all: str = "other") -> list[dict]:
    """Deterministically correct classifications after the LLM pass. Pure.

    ``overrides`` is an ordered list of ``(substring, category_key | None)``. For
    each message, the first substring that appears in the sender email wins and
    sets ``row["category"]``; a target of ``None`` forces ``catch_all`` (which
    must be a label="" category, so the sender is never filtered = excluded).

    This is the guardrail that makes triage robust regardless of think-provider
    strength: a weak model mislabeling a ``...-jobnotification@`` address as a
    newsletter, or anything that must never be filtered (immigration, real
    people), is caught here. Mutates rows in place and returns the same list.
    """
    if not overrides:
        return classified
    for row in classified:
        email = extract_email(row.get("from", "")) or (row.get("from", "") or "").lower()
        for pattern, target in overrides:
            if pattern and pattern in email:
                row["category"] = target if target is not None else catch_all
                break
    return classified


def propose_filters(classified: list[dict], categories: list[Category],
                    *, min_count: int = 1) -> list[dict]:
    """Synthesize Gmail filter rules from classified mail. Pure.

    One rule per (sender address, category) — but ONLY for categories that carry
    a non-empty label. The catch-all (label="") never produces a rule, so an
    inbox manager never proposes a filter for mail it couldn't confidently
    categorize.

    A (sender, category) group is dropped when its count is below the category's
    own ``min_count`` (or the global ``min_count`` floor, whichever is higher) —
    so high-noise categories (newsletters, min_count=2) shed one-off promo senders
    while important categories (finance/job, min_count=1) keep every sender.
    Sorted by how many recent messages each rule would catch.
    """
    by_key = {c.key: c for c in categories}
    groups: dict[tuple, dict] = {}
    for r in classified:
        cat = by_key.get(r.get("category"))
        if cat is None or not cat.label:
            continue
        email = extract_email(r.get("from", ""))
        if not email:
            continue
        key = (email, cat.key)
        g = groups.get(key)
        if g is None:
            groups[key] = {"from": email, "count": 1,
                           "sample_subject": r.get("subject", ""), "cat": cat}
        else:
            g["count"] += 1

    rules = []
    for (email, _), g in groups.items():
        cat = g["cat"]
        floor = max(min_count, getattr(cat, "min_count", 1))
        if g["count"] < floor:
            continue
        rules.append({
            "from": email,
            "category": cat.key,
            "label": cat.label,
            "archive": cat.archive,
            "count": g["count"],
            "sample_subject": g["sample_subject"],
        })
    rules.sort(key=lambda r: (-r["count"], r["from"]))
    return rules


def build_gmail_filters_xml(rules: list[dict], *,
                            title: str = "EmptyOS mail filters") -> str:
    """Render filter rules as a Gmail-importable mailFilters XML (Atom + apps:).

    The user imports this via Gmail → Settings → Filters and Blocked Addresses →
    Import filters, where Gmail shows each rule for confirmation before creating
    it. Pure — all values are XML-escaped; this module only emits, never parses.
    """
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<feed xmlns="http://www.w3.org/2005/Atom" '
        'xmlns:apps="http://schemas.google.com/apps/2006">',
        f"  <title>{escape(title)}</title>",
    ]
    for r in rules:
        frm = (r.get("from") or "").strip()
        label = (r.get("label") or "").strip()
        if not frm or not label:
            continue
        lines.append("  <entry>")
        lines.append("    <category term='filter'></category>")
        lines.append("    <title>Mail Filter</title>")
        lines.append("    <content></content>")
        lines.append(f"    <apps:property name='from' value={quoteattr(frm)}/>")
        lines.append(f"    <apps:property name='label' value={quoteattr(label)}/>")
        if r.get("archive"):
            lines.append("    <apps:property name='shouldArchive' value='true'/>")
        lines.append("  </entry>")
    lines.append("</feed>")
    return "\n".join(lines)


async def gmail_status(svc) -> dict:
    """Connection status for the read-only gmail service. Shared by every app
    that surfaces a gmail connection (the ``mail`` manager + the jobs career lens).

    ``svc`` is the service object (``self.service("gmail")``) or None — dependency-
    injected so this stays testable with a fake. Returns
    {enabled, connected, reason, email}; never raises (a probe failure degrades
    to disconnected, never a 500).
    """
    if svc is None:
        return {"enabled": False, "connected": False,
                "reason": "Gmail plugin not installed.", "email": ""}
    try:
        connected = bool(await svc.available())
    except Exception:
        connected = False
    email = ""
    if connected:
        try:
            prof = await svc.profile()
            email = (prof or {}).get("emailAddress", "")
        except Exception:
            email = ""
    return {
        "enabled": True,
        "connected": connected,
        "reason": "" if connected else
                  "Gmail not connected — run scripts/gmail_auth.py, then restart.",
        "email": email,
    }
