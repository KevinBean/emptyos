"""Shared helpers for single-file HTML artifact builders (viz, designer).

Both apps drive an LLM to emit one standalone HTML document, then strip/validate
it before saving. The extraction + validation logic is identical, so it lives
here (CLAUDE.md rule 9 — second consumer triggers the SDK extraction).

Pure functions only — no `self`, no kernel access, no I/O. Consumers alias the
public names to their existing private ones, e.g.:

    from emptyos.sdk.html_artifact import (
        extract_html as _extract_html,
        looks_truncated as _looks_truncated,
        ...
    )
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime

# A whole-string ```html ... ``` fence.
FENCE_RE = re.compile(r"^```(?:html)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)
# A fenced html block ANYWHERE (model wrote a prose preamble, then the fence).
FENCE_ANYWHERE_RE = re.compile(r"```(?:html)?\s*\n(.*?)```", re.DOTALL)


def strip_fences(text: str) -> str:
    """If the LLM wrapped output in a whole-string ```html ... ``` fence, strip it."""
    text = text.strip()
    m = FENCE_RE.match(text)
    if m:
        return m.group(1).strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
    return text.strip()


def looks_like_html(text: str) -> bool:
    """True if the head looks like an HTML document start (loose: substring scan)."""
    head = text.lstrip()[:200].lower()
    return "<!doctype" in head or "<html" in head


def extract_html(text: str) -> str:
    """Robustly pull the HTML document out of an LLM reply.

    Larger few-shots make gpt-class models wrap the page in a prose preamble
    and/or a ```html fence rather than emit the bare document. Always trim to the
    actual document span so neither leading nor trailing prose survives:
      1. strip a whole-string ```html fence,
      2. prefer a fenced html block anywhere (prose-then-fence),
      3. trim to the first <!doctype/<html through the last </html>.
    A reply with no recoverable document (refusal / truncation) falls through
    unchanged so the caller's validation flags it. Don't short-circuit on
    `looks_like_html` — it matches `<!doctype` *anywhere* in the first 200 chars,
    so a short preamble would otherwise leave the prose + fence markers in.
    """
    t = strip_fences(text)
    m = FENCE_ANYWHERE_RE.search(t)
    if m and ("<!doctype" in m.group(1).lower() or "<html" in m.group(1).lower()):
        t = m.group(1).strip()
    low = t.lower()
    i = low.find("<!doctype")
    if i < 0:
        i = low.find("<html")
    if i >= 0:
        j = low.rfind("</html>")
        t = t[i:(j + 7) if j >= 0 else len(t)]
    return t.strip()


def looks_truncated(text: str) -> tuple[bool, str]:
    """Detect mid-stream truncation. Output token caps bite hardest on full-file
    rewrites; refusing to save a truncated artifact beats silently corrupting it."""
    tail = text.rstrip()[-500:].lower()
    if "</html>" not in tail:
        return True, "missing closing </html> tag — output likely truncated"
    if "</body>" not in tail:
        return True, "missing closing </body> tag — output likely truncated"
    last_char = text.rstrip()[-1:] if text.rstrip() else ""
    if last_char != ">":
        return True, f"output ends with {last_char!r}, not a closing tag — likely truncated"
    return False, ""


def rewrite_user_msg(prior_prompt: str, change: str, prior_html: str, *, preserve_hint: str = "") -> str:
    """Whole-file-rewrite user message for the iterate path.

    `preserve_hint` appends an app-specific clause inside the parenthetical (e.g.
    designer asks to preserve baked `<iframe srcdoc>` embeds; viz passes nothing).
    """
    hint = f"; {preserve_hint}" if preserve_hint else ""
    return (
        f"PRIOR BRIEF:\n{prior_prompt}\n\n"
        f"CHANGE REQUEST:\n{change}\n\n"
        f"CURRENT FILE (rewrite the whole thing applying the change{hint}):\n{prior_html}"
    )


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_artifact_id() -> str:
    # 8 hex chars — collision-resistant at one user's artifact-output scale.
    return secrets.token_hex(4)
