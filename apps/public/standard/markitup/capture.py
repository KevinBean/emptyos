"""markitup — turning a URL into a reviewable shot: pixels plus measured rects.

Extracted from app.py to keep the spine atomic (CLAUDE.md rule 4). Owns: the
host policy that decides what may be fetched, the injected script that stamps
anchors and measures them, and the per-view capture that produces one PNG plus
one anchor sidecar.

The measurement is the reason this app exists rather than a prompt. A model
asked to place a comment on a screenshot guesses coordinates; the artifact that
prompted this app did exactly that. Here the model only ever names an element
from a menu, and the pin position comes from that element's real
``getBoundingClientRect()`` — so a pin is either exactly right or absent, never
plausibly wrong.

Everything returned is in **page CSS pixels**; converting to the 0..1 the UI and
exports use is ``shared.rect_to_point``'s job, against ``origin``. Keeping the
transform out of here is what lets it be tested without a browser.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: shared (a leaf) — slugify + the URL helpers.
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk.html_anchors import EDITABLE_TAGS
from emptyos.sdk.web_search import is_public_web_url

from .shared import authed_url, is_own_daemon, rect_to_point, slugify, strip_token

if TYPE_CHECKING:
    from .app import MarkitupApp  # noqa: F401 — for type hints only


# ─── Bind to MarkitupApp class as ────────────────────────────────────
#   _url_refusal   = _capture._url_refusal
#   capture_view   = _capture.capture_view
#
# is_own_daemon / authed_url / strip_token live in shared.py (pure, cfg passed
# in) so discovery.py can reach them without a helper-to-helper import, and so
# authed_url is NOT a bound method — POST /api/apps/{id}/rpc/{method} dispatches
# any non-underscore attribute, which would publish a token oracle.
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────

# Deliberately NOT "data-eos-el". designer and viz persist artifacts already
# stamped with that attribute, and reviewing one of those is exactly what the
# `design` rubric exists for. Sharing the attribute let a later element inherit
# an id an earlier one had already reported, so a comment about element A was
# pinned onto element B — "confidently wrong", the one failure this app claims
# to make impossible.
ANCHOR_ATTR = "data-mk-el"

# The SDK's editable set plus the form controls it deliberately excludes.
# html_anchors.EDITABLE_TAGS is scoped to elements designer can rewrite as an
# HTML fragment, which is not the same question as "can a reviewer point at it".
# Without these, a review of any form cannot pin its own fields — on the first
# real run against a login page the model wrote "the input it acts on isn't
# present in the anchor menu", which was true and was our omission.
FORM_TAGS = frozenset({"input", "select", "textarea"})
ANCHOR_TAGS = frozenset(EDITABLE_TAGS) | FORM_TAGS

# Ceiling on the anchor menu. A content-heavy page yields thousands of elements;
# past a few hundred the menu costs more tokens than the review is worth and the
# model's recall over it degrades. Document order is kept, so the overflow that
# gets dropped is the page footer rather than the hero.
MAX_ANCHORS = 300

# Page text handed to the review pass. Fenced as untrusted before it reaches a
# prompt — see review.py.
MAX_PAGE_TEXT = 8_000


def _stamp_js(tags: list[str], attr: str, max_anchors: int, max_text: int,
              form_tags: list[str], *, row_anchors: bool = False) -> str:
    """Build the injected measure-and-stamp script.

    Kept as a builder rather than a constant so both tag sets stay sourced from
    Python — ``ANCHOR_TAGS`` (a strict superset of ``html_anchors.EDITABLE_TAGS``,
    adding the form controls the SDK deliberately excludes) and ``FORM_TAGS``.
    Two sets that must agree and are written down twice will drift, which is why
    ``form_tags`` is threaded through rather than hardcoded in the JS body.

    Selection is deliberately narrower than "every element": an element earns a
    place in the menu only if it carries its own text, is an image, or is an
    interactive control. Layout wrappers have rects but nothing a reviewer can
    say anything about, and including them buries the real content.
    """
    return """
(() => {
  const TAGS = %(tags)s;
  const ATTR = %(attr)s;
  const MAX = %(max_anchors)d;
  const ROWS = %(row_anchors)s;
  const FORM = Object.fromEntries(%(form_tags)s.map((t) => [t, 1]));
  const doc = document.documentElement;
  const sx = window.scrollX, sy = window.scrollY;

  const directText = (el) => {
    let n = 0;
    for (const c of el.childNodes) {
      if (c.nodeType === 3) n += (c.textContent || '').trim().length;
    }
    return n;
  };
  const snippet = (el) => {
    // A form control has no text of its own — describe it by whatever the user
    // actually sees ON it, so the anchor menu can name it.
    //
    // NEVER el.value. A password input's value is precisely what the user does
    // NOT see, and /settings/ renders stored secrets straight into value= with
    // no placeholder (github.token, commons.api_token, ...). Reading it here
    // would carry the secret into the anchor menu -> the think prompt -> a
    // cloud provider -> review.json -> an exported PDF in the vault. `name` is
    // the safe terminal label: it identifies the control without disclosing it.
    const t = (el.innerText || el.getAttribute('alt')
               || el.getAttribute('placeholder') || el.getAttribute('aria-label')
               || el.getAttribute('name') || '').trim();
    return t.replace(/\\s+/g, ' ').slice(0, 80);
  };

  const out = [];
  let seq = 0;
  for (const el of document.querySelectorAll(TAGS.join(','))) {
    if (out.length >= MAX) break;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') continue;
    const tag = el.tagName.toLowerCase();
    const interactive = (tag === 'a' || tag === 'button' || tag === 'label');
    // A form control is always worth an anchor: it carries no text of its own,
    // but it is exactly the thing a reviewer points at.
    // A table row has no direct text of its own (it all sits in cells), so the
    // direct-text rule can never admit one. A rendered document is reviewed
    // row by row — "SR-INP-06 is wrong" — so the document producer opts rows
    // in; the page-review path keeps cells, which do carry direct text.
    if (!(FORM[tag] || tag === 'img' || directText(el) > 0
          || (interactive && snippet(el))
          || (ROWS && tag === 'tr' && snippet(el)))) continue;
    // Always mint our own id — never adopt one already on the element.
    const id = 'e' + seq;
    el.setAttribute(ATTR, id);
    seq += 1;
    out.push({
      el: id,
      tag: tag,
      text: snippet(el),
      rect: {x: r.left + sx, y: r.top + sy, w: r.width, h: r.height},
    });
  }

  const bodyText = (document.body ? document.body.innerText || '' : '');
  return {
    anchors: out,
    doc: {w: doc.scrollWidth, h: doc.scrollHeight},
    text: bodyText.replace(/\\n{3,}/g, '\\n\\n').trim().slice(0, %(max_text)d),
    title: document.title || '',
  };
})()
""" % {
        "tags": json.dumps(sorted(tags)),
        "attr": json.dumps(attr),
        "max_anchors": max_anchors,
        "max_text": max_text,
        "form_tags": json.dumps(sorted(form_tags)),
        "row_anchors": "true" if row_anchors else "false",
    }


async def _url_refusal(self, url: str) -> str | None:
    """``None`` if this URL may be fetched, else a human reason to refuse.

    Two things may be reached: the public web, and *this daemon* — reviewing our
    own pages is half the point of the app, and they live on a loopback or
    private address that the SSRF guard correctly rejects for anything else.
    The carve-out is matched on host and port against the daemon's own
    ``[network]`` config rather than on "is it private", so a leased sandbox or
    another machine on the LAN is still refused.
    """
    url = (url or "").strip()
    if not url:
        return "no URL given"
    # Redundant with the SSRF guard below, which also rejects non-http schemes.
    # Kept for the message: "only http(s)" tells the user which mistake they
    # made, where the guard's answer would send them looking at networking.
    if not url.lower().startswith(("http://", "https://")):
        return "only http(s) URLs can be captured"

    from urllib.parse import urlsplit

    # Reject an ambiguous authority BEFORE anything else. urlsplit takes the
    # host after the LAST "@" while WHATWG (Chromium, so Playwright) ends the
    # authority at the FIRST "\\" — so "http://192.168.1.1\@127.0.0.1:9000/"
    # parses here as our own daemon and navigates there as 192.168.1.1.
    # This has to precede the carve-outs: they return early, and the equivalent
    # guard inside is_public_web_url is reached only if they both miss.
    authority = url.split("//", 1)[-1].split("/", 1)[0]
    lowered = authority.lower()
    if "\\" in authority or "%5c" in lowered or "@" in authority:
        return ("that URL's authority is ambiguous (it contains a backslash or "
                "userinfo) — different parsers disagree about which host it names, "
                "so it is refused rather than guessed at")

    try:
        # .port is lazy: urlsplit itself never validates it, so an out-of-range
        # or non-numeric port raises on ACCESS, not on parse. Touching it inside
        # the try is what keeps a bad port an in-band error instead of a 500.
        urlsplit(url).port
    except ValueError:
        return "that URL could not be parsed (check the host and port)"

    if is_own_daemon(self.kernel.config, url):
        return None

    # Blocking (it resolves DNS), so it must not run on the event loop.
    if not await asyncio.to_thread(is_public_web_url, url):
        return (
            "that URL is not on the public web and is not this daemon — "
            "capture refuses private, loopback and link-local addresses"
        )
    return None


async def capture_view(
    self,
    *,
    url: str,
    title: str = "",
    selector: str = "",
    viewport: str = "1440x900",
    focus: str = "",
    context_id: str,
    shots_dir: Path,
    anchor_tags: frozenset | None = None,
    max_anchors: int | None = None,
    anchor_rows: bool = False,
) -> dict:
    """Capture one view: navigate, stamp, measure, screenshot.

    ``anchor_tags`` / ``max_anchors`` let a producer narrow or widen the anchor
    menu for a source it knows the shape of. The defaults are the page-review
    set; a long rendered document wants row-level anchors and a higher ceiling,
    or the bottom of the document loses its pins (document order is kept, so
    the overflow that drops is the tail).

    The shot is always the WHOLE page. A selector used to crop it, and that put
    the anchor menu and the captured region in different coordinate spaces: the
    menu is harvested page-wide, so a ``header`` crop — 1440x53 on the site that
    surfaced this — offered 163 elements of which 7 could pin, and the model was
    invited to comment on things it had no way to anchor. Every one of those
    comments then arrived unpinned, which reads as the model failing when it was
    the capture that was wrong.

    ``selector`` survives on the record and reaches the review pass as a hint at
    the region worth attending to. It no longer decides what is captured.

    Returns a shot record. Raises ``ValueError`` when the URL is refused.
    """
    refusal = await _url_refusal(self, url)
    if refusal:
        raise ValueError(refusal)

    nav = await self.browse(
        "navigate", url=authed_url(self.kernel.config, url), wait="load",
        viewport=viewport, context_id=context_id
    )
    applied = nav.get("viewport") or {}
    landed = strip_token(nav.get("url") or url)

    measured = (await self.browse(
        "eval",
        expression=_stamp_js(sorted(anchor_tags or ANCHOR_TAGS), ANCHOR_ATTR,
                             int(max_anchors or MAX_ANCHORS),
                             MAX_PAGE_TEXT, sorted(FORM_TAGS),
                             row_anchors=anchor_rows),
        context_id=context_id,
    )).get("value") or {}

    anchors = measured.get("anchors") or []
    doc = measured.get("doc") or {}

    origin = {"x": 0, "y": 0, "w": doc.get("w") or 0, "h": doc.get("h") or 0}

    # The invariant that makes a menu id mean anything: never offer an element
    # that cannot resolve to a pin in THIS shot. `rect_to_point` is the same
    # predicate `attach_points` uses at placement time, so the menu and the
    # pinning are physically unable to disagree about what is reachable — which
    # a hand-written intersection test here would eventually do.
    #
    # Whole-page capture makes this a near no-op today (an element is inside the
    # document it was measured in). It is kept because it is the guard, not the
    # fix: the day anything reintroduces a crop, the menu narrows with it
    # instead of silently promising ids that cannot land.
    anchors = [a for a in anchors
               if isinstance(a, dict) and rect_to_point(a.get("rect"), origin) is not None]

    shot_id = f"{slugify(title or nav.get('title') or 'view')}-{uuid.uuid4().hex[:6]}"
    shots_dir.mkdir(parents=True, exist_ok=True)
    png = shots_dir / f"{shot_id}.png"
    await self.browse(
        "screenshot",
        full_page=True,
        path=str(png),
        context_id=context_id,
    )

    # Anchors live in a sidecar, not in review.json: a few hundred rects per
    # shot would dominate the review document, and after the review pass has
    # run they are only needed to re-review or re-place a pin.
    (shots_dir / f"{shot_id}.anchors.json").write_text(
        json.dumps({"anchors": anchors, "origin": origin, "measured_by": "dom"},
                   ensure_ascii=False),
        encoding="utf-8",
    )

    return {
        "id": shot_id,
        "title": (title or measured.get("title") or nav.get("title") or url).strip(),
        "url": landed,
        "selector": selector,
        "viewport": {"w": applied.get("width"), "h": applied.get("height")},
        "focus": focus,
        "image": png.name,
        "origin": origin,
        "anchor_count": len(anchors),
        "text": measured.get("text") or "",
    }
