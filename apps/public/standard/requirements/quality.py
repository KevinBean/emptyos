"""requirements — wording checks on a requirement statement.

Deterministic and pure: no model call, no I/O. A statement a reviewer cannot
verify, or that hides two obligations in one sentence, fails at the test that
should prove it — these checks flag the shapes that cause that while the
statement is still cheap to change. They advise; nothing is blocked or rewritten.

Each finding: ``{"code", "severity", "message", "match"}`` where ``match`` is the
text that triggered it (shown to the reader, so they can find it).

Calibrated 2026-10-03 on the mock substation spec (`mock-substation-compliance`,
20 statements) and on 3,567 shall/must sentences from the KB's clause notes.
"may" is deliberately not flagged: in standards usage it grants permission
("the Contractor may propose an alternative"), and 20 of 20 sampled KB hits
meant exactly that — "use shall" there would change the meaning.
"""

from __future__ import annotations

import re

# An obligation is stated with shall/must. A statement with neither is a
# description, an intent, or a fact — none of which a test can pass or fail.
_OBLIGATION_RE = re.compile(r"\b(shall|must)\b", re.IGNORECASE)
_PROHIBITION_RE = re.compile(r"\b(not permitted|prohibited|not allowed|forbidden)\b", re.IGNORECASE)
_PERMISSION_RE = re.compile(r"\bmay\b(?!\s+\d)", re.IGNORECASE)   # not "May 2027"

# "should" is a recommendation. It is a finding only when the requirement's
# priority says otherwise (or is unknown) — a `should` requirement written
# with "should" is consistent, not wrong.
_SHOULD_RE = re.compile(r"\bshould\b", re.IGNORECASE)
# Conditional "should" means "if": "should he elect to do so", "Should the cable
# be installed …", "(should single-core cables be used)". Measured as the bulk of
# the remaining weak-modal hits on the KB clause notes.
_CONDITIONAL_SHOULD_RE = re.compile(
    r"(?:^|[(;:,.]\s*)should\b|\bshould\s+(?:he|she|it|they|the|a|an|any|this|that|these|those|there|single|either)\b",
    re.IGNORECASE)

# Words with no test behind them: whoever verifies has to decide what they
# mean. Inflected and hyphenated forms are listed explicitly because the match
# refuses to run into a neighbouring letter or hyphen.
_VAGUE_TERMS = (
    "as appropriate", "as necessary", "where possible", "if possible",
    "where practicable", "as far as possible", "adequate", "adequately", "sufficient",
    "sufficiently", "suitable", "suitably", "appropriate", "appropriately", "reasonable",
    "reasonably", "user-friendly", "user friendly", "easy", "easily", "easy-to-use",
    "fast", "quickly", "efficient", "efficiently", "robust",
    "minimise", "minimised", "minimises", "minimising", "minimize", "minimized",
    "minimizes", "minimizing", "maximise", "maximised", "maximises", "maximising",
    "maximize", "maximized", "maximizes", "maximizing", "approximately", "etc", "and/or",
    "but not limited to", "state of the art", "state-of-the-art", "best practice",
)
# Deliberately absent: "flexible" (a flexible braid is a product, REQ-010 of the
# mock spec) and "normal" ("normal operating conditions" is a defined term in
# most standards).
_VAGUE_RE = re.compile(
    r"(?<![\w-])(" + "|".join(re.escape(t) for t in sorted(_VAGUE_TERMS, key=len, reverse=True))
    + r")(?![\w-])", re.IGNORECASE)
# "as required" is vague on its own and precise when it names the authority
# ("as required by AS 2067").
_AS_REQUIRED_RE = re.compile(r"(?<![\w-])as required(?!\s+by\b)(?![\w-])", re.IGNORECASE)
# A quantity adjective followed closely by a number has its criterion right
# there: "sufficient to withstand 25 kA for 1 s".
_QUANTIFIABLE = {"sufficient", "sufficiently", "adequate", "adequately", "suitable", "suitably",
                 "approximately"}
_NUMBER_SOON_RE = re.compile(r"^\W*(?:\S+\s+){0,5}?\S*\d")

# Placeholders: the requirement is not finished. Chinese forms too, since a
# placeholder check is the one rule that applies to a Chinese statement.
_PLACEHOLDER_RE = re.compile(
    r"\b(TBD|TBC|TBA|to be (?:determined|confirmed|advised|agreed))\b|\?{2,}|？{2,}|待定|待确认|待补充",
    re.IGNORECASE)

# The wording rules are English. A mostly-Chinese statement says "shall" with
# 应 / 必须, but 应 also sits inside 应用 / 响应, so a character match would
# misfire; such statements get only the placeholder check. Mostly-English
# statements quoting a Chinese term are still checked.
_CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def _mostly_cjk(text: str) -> bool:
    cjk = len(_CJK_RE.findall(text))
    return cjk > 0 and cjk * 2 >= len(_LATIN_RE.findall(text))


def lint_statement(text: str, priority: str | None = None) -> list[dict]:
    """Wording findings for one requirement statement.

    ``priority`` is the requirement's MoSCoW priority when known: "should" in
    the text is then a finding only if the priority is not also "should".
    """
    text = (text or "").strip()
    if not text:
        return [{"code": "empty", "severity": "error",
                 "message": "No statement — there is nothing to verify.", "match": ""}]
    if _mostly_cjk(text):
        return _placeholders(text)
    out: list[dict] = []
    obligations = _OBLIGATION_RE.findall(text)
    # Where each conditional "should" starts, so only the recommendations remain.
    conditional = {c.start() + c.group(0).lower().index("should")
                   for c in _CONDITIONAL_SHOULD_RE.finditer(text)}
    shoulds = [m for m in _SHOULD_RE.finditer(text) if m.start() not in conditional]
    # With "should" present its own finding already explains the missing
    # obligation; reporting "no shall" beside it is the same fact twice.
    if not obligations and not shoulds:
        if _PROHIBITION_RE.search(text):
            msg = "A prohibition without “shall not” — write it as “… shall not …” so it reads as an obligation."
        elif _PERMISSION_RE.search(text):
            msg = ("A permission (“may”), not an obligation — right if intended, but there is "
                   "nothing here for a test to verify.")
        else:
            msg = "No “shall” or “must” — it reads as a description, which a test cannot pass or fail."
        out.append({"code": "no-obligation", "severity": "warn", "message": msg, "match": ""})
    if len(obligations) > 1:
        out.append({"code": "compound", "severity": "warn",
                    "message": f"{len(obligations)} obligations in one statement — split it so each can be verified on its own.",
                    "match": ", ".join(o.lower() for o in obligations)})
    if (priority or "").lower() != "should":
        for m in shoulds:
            out.append({"code": "weak-modal", "severity": "warn",
                        "message": (f"“{m.group(0)}” is a recommendation, but the priority is “{priority}” — "
                                    "use “shall”, or set the priority to should.") if priority else
                                   f"“{m.group(0)}” is a recommendation — use “shall” if this is required.",
                        "match": m.group(0)})
    vague = [m for m in _VAGUE_RE.finditer(text)
             if not (m.group(0).lower() in _QUANTIFIABLE and _NUMBER_SOON_RE.match(text[m.end():]))]
    vague += list(_AS_REQUIRED_RE.finditer(text))
    for m in sorted(vague, key=lambda m: m.start()):
        out.append({"code": "vague", "severity": "warn",
                    "message": f"“{m.group(0)}” has no measurable meaning — state the value or condition a test can check.",
                    "match": m.group(0)})
    return out + _placeholders(text)


def _placeholders(text: str) -> list[dict]:
    return [{"code": "placeholder", "severity": "error",
             "message": f"“{m.group(0)}” — the requirement is not finished.",
             "match": m.group(0)} for m in _PLACEHOLDER_RE.finditer(text)]
