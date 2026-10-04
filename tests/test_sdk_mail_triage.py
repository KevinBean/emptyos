"""Unit tests for emptyos/sdk/mail_triage.py — the shared Gmail-triage primitives
(chunked classifier + Gmail filter XML). Pure / async-logic only, no daemon."""

from __future__ import annotations

import asyncio
import json
import xml.etree.ElementTree as ET

from emptyos.sdk.mail_triage import (
    Category,
    apply_overrides,
    build_classify_system,
    build_gmail_filters_xml,
    catch_all_key,
    classify_messages,
    extract_email,
    gmail_status,
    propose_filters,
)

CATS = [
    Category("newsletter", "marketing digests", label="Newsletters", archive=True),
    Category("personal", "a real person", label="Personal"),
    Category("other", "anything else", label=""),   # catch-all
]


def _run(coro):
    return asyncio.run(coro)


class _Think:
    """Stub think_fn returning a canned JSON string; counts calls."""
    def __init__(self, canned):
        self.canned = canned
        self.calls = 0
    async def __call__(self, prompt, system):
        self.calls += 1
        return self.canned


MSGS = [
    {"id": "m1", "from": "News <n@news.com>", "subject": "Digest", "snippet": "top"},
    {"id": "m2", "from": "Mum <mum@home.com>", "subject": "Hi", "snippet": "call me"},
]


# ── catch_all_key ────────────────────────────────────────────────────────────

def test_catch_all_is_empty_label():
    assert catch_all_key(CATS) == "other"

def test_catch_all_falls_back_to_last():
    cats = [Category("a", "x", label="A"), Category("b", "y", label="B")]
    assert catch_all_key(cats) == "b"


# ── build_classify_system ────────────────────────────────────────────────────

def test_system_prompt_lists_categories_and_extra_fields():
    s = build_classify_system(CATS, extra_fields={"company": "the hiring company"})
    assert "newsletter: marketing digests" in s
    assert "company" in s
    assert '"category"' in s


# ── classify_messages (async) ────────────────────────────────────────────────

def test_classify_aligns_and_maps():
    canned = json.dumps([
        {"n": 1, "category": "newsletter", "confidence": 0.9, "reason": "digest"},
        {"n": 2, "category": "personal", "confidence": 0.8, "reason": "from mum"},
    ])
    out = _run(classify_messages(_Think(canned), MSGS, CATS))
    assert [r["category"] for r in out] == ["newsletter", "personal"]
    assert out[0]["id"] == "m1"

def test_classify_unknown_category_to_catch_all():
    canned = json.dumps([{"n": 1, "category": "bogus", "confidence": 0.5, "reason": "?"},
                         {"n": 2, "category": "personal", "confidence": 0.5, "reason": "x"}])
    out = _run(classify_messages(_Think(canned), MSGS, CATS))
    assert out[0]["category"] == "other"      # unknown → catch-all
    assert out[1]["category"] == "personal"

def test_classify_missing_row_to_catch_all():
    canned = json.dumps([{"n": 1, "category": "newsletter", "confidence": 0.5, "reason": "x"}])
    out = _run(classify_messages(_Think(canned), MSGS, CATS))
    assert len(out) == 2
    assert out[1]["category"] == "other"      # missing → catch-all, never dropped

def test_classify_garbage_all_catch_all():
    out = _run(classify_messages(_Think("not json"), MSGS, CATS))
    assert all(r["category"] == "other" for r in out)

def test_classify_extra_fields_returned():
    cats = [Category("job", "job mail", label="Jobs"), Category("other", "x", label="")]
    canned = json.dumps([{"n": 1, "category": "job", "confidence": 0.7,
                          "reason": "recruiter", "company": "Acme"},
                         {"n": 2, "category": "other", "confidence": 0.5, "reason": "x"}])
    out = _run(classify_messages(_Think(canned), MSGS, cats, extra_fields={"company": "co"}))
    assert out[0]["company"] == "Acme"
    assert out[1]["company"] == ""            # absent extra field → ""

def test_classify_chunks_large_batches():
    n = 45
    msgs = [{"id": f"m{i}", "from": "x@y.com", "subject": "s", "snippet": ""} for i in range(n)]
    think = _Think(json.dumps([{"n": 1, "category": "personal", "confidence": 0.5, "reason": "x"}]))
    out = _run(classify_messages(think, msgs, CATS, chunk_size=20))
    assert len(out) == n
    assert think.calls == 3                    # 45 / 20 → 3 chunks


# ── extract_email ────────────────────────────────────────────────────────────

def test_extract_email():
    assert extract_email("LinkedIn <a@linkedin.com>") == "a@linkedin.com"
    assert extract_email("plain@host.com") == "plain@host.com"
    assert extract_email("No Address Here") == ""
    assert extract_email("") == ""


# ── propose_filters ──────────────────────────────────────────────────────────

def _row(frm, cat, subj="s"):
    return {"from": frm, "category": cat, "subject": subj}

def test_propose_skips_catch_all():
    rows = [_row("News <n@news.com>", "other"), _row("X <x@x.com>", "other")]
    assert propose_filters(rows, CATS) == []   # catch-all never filtered

def test_propose_groups_labels_and_archive():
    rows = [
        _row("News <n@news.com>", "newsletter"),
        _row("News <n@news.com>", "newsletter"),
        _row("Mum <mum@home.com>", "personal"),
        _row("Junk <j@j.com>", "other"),
    ]
    rules = propose_filters(rows, CATS)
    assert len(rules) == 2                      # other dropped
    top = rules[0]
    assert top["from"] == "n@news.com"
    assert top["label"] == "Newsletters"
    assert top["archive"] is True
    assert top["count"] == 2
    personal = [r for r in rules if r["category"] == "personal"][0]
    assert personal["archive"] is False


# ── min_count trim ───────────────────────────────────────────────────────────

TRIM_CATS = [
    Category("promo", "marketing", label="Newsletters", archive=True, min_count=2),
    Category("finance", "money", label="Finance", min_count=1),
    Category("other", "rest", label=""),
]

def test_propose_min_count_drops_one_off_newsletters():
    rows = [
        _row("A <a@x.com>", "promo"),                 # 1× promo → below min_count=2
        _row("B <b@y.com>", "promo"), _row("B <b@y.com>", "promo"),  # 2× → kept
        _row("Bank <bank@z.com>", "finance"),         # 1× finance → kept (min_count=1)
    ]
    rules = propose_filters(rows, TRIM_CATS)
    froms = {r["from"] for r in rules}
    assert "a@x.com" not in froms                 # one-off promo trimmed
    assert "b@y.com" in froms                      # recurring promo kept
    assert "bank@z.com" in froms                   # finance singleton kept

def test_propose_global_min_count_floor():
    rows = [_row("B <b@y.com>", "finance")]        # 1× finance, but global floor=2
    assert propose_filters(rows, TRIM_CATS, min_count=2) == []


# ── apply_overrides ──────────────────────────────────────────────────────────

def _crow(frm, cat):
    return {"from": frm, "category": cat}

def test_overrides_rescue_to_target():
    rows = [_crow("X <people@acme-jobnotification.com>", "newsletter")]
    apply_overrides(rows, [("jobnotification", "job_search")])
    assert rows[0]["category"] == "job_search"

def test_overrides_exclude_to_catch_all():
    rows = [_crow("Visa <no-reply@skillselect.gov.au>", "newsletter")]
    apply_overrides(rows, [("skillselect.gov.au", None)], catch_all="other")
    assert rows[0]["category"] == "other"          # → label="" → never filtered

def test_overrides_first_match_wins():
    rows = [_crow("X <jobmail@seek.com.au>", "newsletter")]
    apply_overrides(rows, [("seek.com", "job_search"), ("jobmail", "social")])
    assert rows[0]["category"] == "job_search"      # first pattern wins

def test_overrides_no_match_unchanged():
    rows = [_crow("X <random@nowhere.com>", "newsletter")]
    apply_overrides(rows, [("jobnotification", "job_search")])
    assert rows[0]["category"] == "newsletter"

def test_overrides_empty_is_noop():
    rows = [_crow("X <a@b.com>", "newsletter")]
    assert apply_overrides(rows, []) == rows
    assert rows[0]["category"] == "newsletter"

def test_overrides_then_propose_excludes_immigration():
    # End-to-end: an immigration sender overridden to catch-all yields no filter.
    rows = [_crow("Visa <x@skillselect.gov.au>", "newsletter"),
            _crow("News <n@news.com>", "newsletter")]
    apply_overrides(rows, [("skillselect.gov.au", None)])
    rules = propose_filters(rows, CATS)
    assert all("skillselect" not in r["from"] for r in rules)


# ── build_gmail_filters_xml ──────────────────────────────────────────────────

def test_xml_well_formed_and_archive_count():
    rules = [
        {"from": "a@b.com", "label": "Personal", "archive": False},
        {"from": "n@news.com", "label": "Newsletters", "archive": True},
    ]
    xml = build_gmail_filters_xml(rules)
    root = ET.fromstring(xml)                    # self-generated, trusted; emits-only in prod
    entries = root.findall("{http://www.w3.org/2005/Atom}entry")
    assert len(entries) == 2
    assert xml.count("shouldArchive") == 1

def test_xml_escapes_and_skips_incomplete():
    xml = build_gmail_filters_xml(
        [{"from": 'a"&<>@b.com', "label": "A & B", "archive": False},
         {"from": "", "label": "X"}],
        title="T & T")
    ET.fromstring(xml)                           # must still parse
    assert xml.count("<entry>") == 1             # incomplete rule skipped


# ── gmail_status (async, fake service) ───────────────────────────────────────

class _Svc:
    def __init__(self, avail, email=None, raise_avail=False, raise_prof=False):
        self._avail, self._email = avail, email
        self._ra, self._rp = raise_avail, raise_prof
    async def available(self):
        if self._ra:
            raise RuntimeError("probe failed")
        return self._avail
    async def profile(self):
        if self._rp:
            raise RuntimeError("profile failed")
        return {"emailAddress": self._email}

def test_gmail_status_no_service():
    out = _run(gmail_status(None))
    assert out["enabled"] is False and out["connected"] is False

def test_gmail_status_not_connected():
    out = _run(gmail_status(_Svc(False)))
    assert out["enabled"] is True and out["connected"] is False
    assert "gmail_auth" in out["reason"]

def test_gmail_status_connected():
    out = _run(gmail_status(_Svc(True, email="a@b.com")))
    assert out["connected"] is True and out["email"] == "a@b.com" and out["reason"] == ""

def test_gmail_status_swallows_probe_errors():
    assert _run(gmail_status(_Svc(True, raise_avail=True)))["connected"] is False
    out = _run(gmail_status(_Svc(True, raise_prof=True)))
    assert out["connected"] is True and out["email"] == ""   # profile error → email ""
