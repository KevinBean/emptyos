"""html_element_edit — Open-Canvas-style scoped single-element HTML edit.

Pure orchestration on top of ``emptyos.sdk.html_anchors``: given a generated
HTML document and the ``data-eos-el`` anchor of one element, produce a
*replacement for that one element* (an LLM rewrite OR a deterministic style
knob), validate it, splice it back, and return the full new document. The
caller owns persistence, staleness, events, and the propose/preview/confirm
gate (``emptyos.sdk.sandbox.SandboxedWrite``).

This is the shared core behind two consumers:
  - ``apps/public/standard/designer`` — element edit on a generated web page.
  - ``apps/public/standard/viz`` — element edit on a DOM-structured artifact
    (svg-diagram / schematic / slide-deck / …; canvas-rendered shapes like
    3d-scene have no addressable element and don't use this).

Lineage: LangChain's Open Canvas "highlight-to-edit" (select a region,
regenerate only it). EmptyOS already had designer's loop; this lifts the
orchestration out so viz can be the second consumer (CLAUDE.md rule 9). See
``.claude/rules/artifact-element-edit.md``.

Pure functions only — no ``self``, no kernel access, no I/O. The LLM call is
injected as ``think_fn(system, user) -> str`` so this module stays kernel-free
and unit-testable without a daemon (tests/test_unit_html_element_edit.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from emptyos.sdk.html_anchors import extract_element, merge_inline_style, outer_tag_name
from emptyos.sdk.html_artifact import strip_fences

# Anchor attribute + the id shape inject_anchors stamps ("e" + sequence number).
ANCHOR_ATTR = "data-eos-el"
ANCHOR_RE = re.compile(r"^e\d+$")

# Knob → (CSS property, validator). Each knob is one inline-style declaration,
# applied deterministically (no LLM). Values are constrained so a knob can never
# inject arbitrary CSS / markup into the page.
_KNOB_PROPS = {"color", "background", "font-size", "padding", "text-align"}
_TEXT_ALIGN = {"left", "center", "right", "justify"}
# A safe CSS value: hex colour, a few keyword shapes, or a <number><unit>.
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")
_LEN_RE = re.compile(r"^\d{1,4}(px|rem|em|%)$")


ELEMENT_EDIT_SYSTEM = """\
You are editing ONE element inside an existing HTML page. You are given that
element's current outerHTML and a change request. Return ONLY the replacement
outerHTML for that single element.

RULES
- Return the element and nothing else: no <html>/<head>/<body>, no surrounding
  page, no other elements, no prose, no markdown code fences.
- Keep the SAME outermost tag as the original (a <p> stays a <p>, a <div> stays a
  <div>).
- Preserve the original `data-eos-el` attribute exactly — it must remain on the
  outermost tag.
- Preserve any child content the change does not concern.
- No em-dashes (the long dash) anywhere — use a period, comma, colon, or spaced
  hyphen.
- Inline any styling on the element itself (style="..."), since the element is
  spliced back into a page whose stylesheet you cannot see.
"""


@dataclass
class ElementEdit:
    """Result of a scoped element edit proposal.

    ``ok`` True → ``new_html`` is the full document with the one element
    replaced; the caller stages it (e.g. SandboxedWrite) and previews the diff.
    ``ok`` False → ``error`` carries a human reason; ``fallback == "iterate"``
    means the element couldn't be resolved precisely and the caller should offer
    its whole-file rewrite path instead of splicing a wrong span.
    """

    ok: bool
    new_html: str = ""
    el: str = ""
    error: str = ""
    fallback: str = ""


def knob_value_ok(prop: str, value: str) -> bool:
    """True if ``value`` is a safe value for the constrained knob ``prop``."""
    value = (value or "").strip()
    if not value:
        return False
    if prop == "text-align":
        return value.lower() in _TEXT_ALIGN
    if prop in ("color", "background"):
        return bool(_HEX_RE.match(value))
    if prop in ("font-size", "padding"):
        return bool(_LEN_RE.match(value))
    return False


def apply_knob(outer_html: str, knob: dict) -> tuple[str, str]:
    """Deterministic single-property style edit. Returns ``(replacement, error)``."""
    prop = str((knob or {}).get("prop") or "").strip().lower()
    value = str((knob or {}).get("value") or "").strip()
    if prop not in _KNOB_PROPS:
        return "", f"unsupported knob '{prop}'"
    if not knob_value_ok(prop, value):
        return "", f"invalid value for {prop!r}"
    return merge_inline_style(outer_html, prop, value), ""


def validate_replacement(repl: str, picked_tag: str, anchor: str, *, anchor_attr: str = ANCHOR_ATTR) -> str:
    """Return "" if ``repl`` is a safe single-element replacement, else a reason."""
    if not repl:
        return "empty replacement"
    low = repl.lower()
    if "<html" in low or "<!doctype" in low:
        return "replacement is a whole document, not one element"
    if f'{anchor_attr}="{anchor}"' not in repl:
        return f"replacement dropped its {anchor_attr} anchor"
    got = outer_tag_name(repl)
    if got != picked_tag:
        return f"replacement changed the outer tag ({picked_tag} → {got or '?'})"
    return ""


def _build_instruction_user_msg(outer_html: str, instruction: str, style_hint: str) -> str:
    style_line = (
        f"\nThe page follows the '{style_hint}' design system; stay consistent with it."
        if style_hint
        else ""
    )
    return (
        f"CHANGE REQUEST:\n{instruction.strip()}\n\n"
        f"CURRENT ELEMENT (return its replacement, same outermost tag):\n{outer_html}"
        f"{style_line}"
    )


async def propose_element_edit(
    html: str,
    el_id: str,
    *,
    instruction: str = "",
    knob: Optional[dict] = None,
    think_fn: Callable[[str, str], Awaitable[str]],
    style_hint: str = "",
    anchor_attr: str = ANCHOR_ATTR,
) -> ElementEdit:
    """Resolve ``el_id`` in ``html``, produce a replacement, splice, return.

    Two kinds, one pipeline:
      - ``knob`` (color/background/font-size/padding/text-align) → deterministic
        inline-style merge, no LLM.
      - ``instruction`` (natural language) → ``think_fn(ELEMENT_EDIT_SYSTEM, user)``
        rewrites just that element; the reply is fence-stripped and validated.

    Never raises — every failure is an ``ElementEdit(ok=False, ...)``. An element
    that can't be unambiguously resolved (auto-closed/unclosed markup) returns
    ``fallback="iterate"``; a srcdoc/iframe target returns a plain error (an
    opaque embed can't be element-edited).
    """
    el_id = (el_id or "").strip()
    if not el_id or not ANCHOR_RE.match(el_id):
        return ElementEdit(ok=False, error="bad element anchor")
    if not instruction and not knob:
        return ElementEdit(ok=False, error="instruction or knob is required")

    span = extract_element(html, el_id, attr=anchor_attr)
    if span is None:
        # Ambiguous markup — don't splice a wrong span; let the caller offer the
        # whole-file iterate path instead.
        return ElementEdit(ok=False, el=el_id, fallback="iterate",
                           error="could not resolve that element precisely")
    s, e = span
    outer = html[s:e]
    low = outer.lower()
    if "srcdoc=" in low or "<iframe" in low:
        return ElementEdit(ok=False, el=el_id,
                           error="embedded content can't be edited here — regenerate it via the brief")

    picked_tag = outer_tag_name(outer)

    if knob:
        repl, err = apply_knob(outer, knob)
        if err:
            return ElementEdit(ok=False, el=el_id, error=err)
    else:
        user = _build_instruction_user_msg(outer, instruction, style_hint)
        raw = await think_fn(ELEMENT_EDIT_SYSTEM, user)
        repl = strip_fences(raw if isinstance(raw, str) else str(raw)).strip()

    reason = validate_replacement(repl, picked_tag, el_id, anchor_attr=anchor_attr)
    if reason:
        return ElementEdit(ok=False, el=el_id, error=reason)

    new_html = html[:s] + repl + html[e:]
    return ElementEdit(ok=True, new_html=new_html, el=el_id)
