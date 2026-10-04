"""markitup — pure helpers: the coordinate transform and small coercions.

No ``self``, no kernel access, no I/O. Everything here is unit-tested without a
daemon (tests/test_unit_markitup.py), which is why the coordinate transform —
the one genuinely new idea in this app — lives here rather than inside the
capture stage where it would need a browser to exercise.

**The coordinate model.** A comment's ``(x, y)`` is normalised 0..1 against the
*captured region*, never against the image's pixel dimensions. That distinction
is load-bearing: a selector-scoped shot is a crop, so an element's page-space
rect has to be translated by the crop origin before it means anything, and
normalising against the region rather than the bitmap makes the transform
independent of device pixel ratio. Get this wrong and every pin on a
selector-scoped shot lands in the wrong place while every full-page shot looks
perfect — exactly the asymmetry that survives a casual review.

A true leaf: imports nothing from its sibling modules, so every helper may
import *from* here (.claude/rules/multi-module-apps.md rule 6). Comment
validation lives in ``rubrics.py`` instead, because it is rubric logic and
moving it keeps that edge from pointing the wrong way.
"""

from __future__ import annotations

import base64
import math
import re

from emptyos.nethost import canonical_hostname

# How far inside an element's top-left corner the pin sits, in PAGE PIXELS —
# converted per-axis at use, so it is isotropic.
#
# It was a fraction of the captured region (0.004) until a hostile review showed
# the arithmetic did not match its own citation: designer-annotate.js nudges by
# 1 absolute pixel, while a fraction of a 1440x4000 shot is 5.8px across and
# 16px down. On a 24px-tall heading that put the pin below the element it named
# — "plausibly wrong", which is the one failure this app exists to prevent.
PIN_INSET_PX = 2.0


def clamp01(v: float) -> float:
    """Clamp to [0, 1]; non-finite input collapses to 0.0."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(f):
        return 0.0
    return 0.0 if f < 0.0 else (1.0 if f > 1.0 else f)


def _num(v) -> float | None:
    """A finite float, or None. Rejects bools — ``isinstance(True, int)`` is
    True in Python, and a rect field of ``True`` must not become 1.0."""
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _box(d) -> tuple[float, float, float, float] | None:
    """Coerce ``{x, y, w, h}`` to a tuple, or None if any field is unusable.

    A zero-area box is rejected: it cannot be intersected meaningfully and, as a
    divisor, would silently produce infinities downstream.
    """
    if not isinstance(d, dict):
        return None
    x, y = _num(d.get("x")), _num(d.get("y"))
    w, h = _num(d.get("w")), _num(d.get("h"))
    if x is None or y is None or w is None or h is None:
        return None
    if w <= 0 or h <= 0:
        return None
    return x, y, w, h


def rect_to_point(rect, origin, *, inset_px: float = PIN_INSET_PX) -> tuple[float, float] | None:
    """Normalised pin position for ``rect`` within the captured ``origin`` region.

    Both boxes are ``{x, y, w, h}`` in page CSS pixels. Returns ``(x, y)`` in
    0..1, or ``None`` when the element does not intersect the captured region at
    all — the signal that this element is not visible in this shot, so the caller
    drops the pin rather than clamping it to an edge and implying the comment
    points at something it does not.
    """
    r, o = _box(rect), _box(origin)
    if r is None or o is None:
        return None
    rx, ry, rw, rh = r
    ox, oy, ow, oh = o
    # Reject only a genuine non-intersection; a partially-visible element still
    # earns a pin, clamped into the region.
    if rx + rw <= ox or ry + rh <= oy or rx >= ox + ow or ry >= oy + oh:
        return None
    # The inset is added in page pixels BEFORE normalising, so the same nudge
    # applies on both axes regardless of how tall the captured region is.
    return (
        clamp01((rx - ox + inset_px) / ow),
        clamp01((ry - oy + inset_px) / oh),
    )


_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(text: str, *, fallback: str = "view", max_len: int = 48) -> str:
    """Lowercase ascii slug for a filename or shot id."""
    s = _SLUG_STRIP.sub("-", (text or "").strip().lower()).strip("-")
    return s[:max_len].strip("-") or fallback


def attach_points(comments: list[dict], rects_by_el: dict, origin) -> list[dict]:
    """Resolve each comment's ``el`` to a pin position via its captured rect.

    Sets ``x``/``y``/``anchor`` in place and returns the list. ``anchor`` becomes
    ``"dom"`` only when a real rect resolved — anything else stays ``"none"``, so
    the UI can say which pins are measured and which are not, and so the vision
    pass knows exactly which comments still need placing.
    """
    rects = rects_by_el or {}
    for c in comments:
        el = c.get("el") or ""
        pt = rect_to_point(rects.get(el), origin) if el else None
        if pt is None:
            c["x"], c["y"], c["anchor"] = None, None, "none"
        else:
            c["x"], c["y"], c["anchor"] = round(pt[0], 5), round(pt[1], 5), "dom"
    return comments


def anchor_menu(anchors, *, max_text: int = 80) -> str:
    """One line per measured anchor: ``- e12 <h1> "Some heading"``.

    This menu is the only place a valid ``el`` can come from, which is what
    makes ``normalize_comments`` rejecting an unknown id meaningful rather than
    punitive — the model was shown every id it is allowed to use.
    """
    lines = []
    for a in anchors or []:
        if not isinstance(a, dict):
            continue
        el = (a.get("el") or "").strip()
        if not el:
            continue
        tag = (a.get("tag") or "").strip() or "?"
        text = " ".join((a.get("text") or "").split())[:max_text]
        lines.append(f"- {el} <{tag}>" + (f' "{text}"' if text else ""))
    return "\n".join(lines)


# Shortest title / body treated as copied when it occurs verbatim in the system
# prompt. Long enough that an ordinary phrase ("Units not stated") cannot match
# by chance; short enough to catch every example line the prompts ship.
ECHO_MIN_TITLE = 24
ECHO_MIN_BODY = 40
# An angle-bracket fragment, the shape of the requirements example's
# placeholders. Removed only when that exact fragment is in the prompt sent.
_PLACEHOLDER_RE = re.compile(r"<[^<>\n]{3,120}>")


def _folded(text) -> str:
    return " ".join(str(text or "").split()).casefold()


def scrub_prompt_echo(row, system: str) -> tuple[dict | None, bool]:
    """Remove what a reply row copied from the system prompt.

    Returns ``(row, changed)``; ``row`` is ``None`` when nothing of the
    reviewer's own remains. A body lifted from the prompt drops the row — the
    finding itself is the copy. A lifted title is only cleared, so a real body
    under a template title survives (the normaliser titles it from the body).
    Placeholder fragments the prompt contains (``<requirement id>``) are cut
    out of a half-filled title or body, so template text never reaches a
    stored comment or an export.

    Checked against the prompt actually sent, not a hard-coded example, so a
    prompt tuned at ``/prompts`` is covered by the same rule.
    """
    if not isinstance(row, dict):
        return row, False
    hay = _folded(system)

    def lifted(text, floor: int) -> bool:
        value = _folded(text)
        return len(value) >= floor and value in hay

    if lifted(row.get("body"), ECHO_MIN_BODY):
        return None, True
    out, changed = dict(row), False
    if lifted(out.get("title"), ECHO_MIN_TITLE):
        out["title"], changed = "", True
    for field in ("title", "body"):
        value = out.get(field)
        if not isinstance(value, str):
            continue
        cleaned = _PLACEHOLDER_RE.sub(
            lambda m: "" if _folded(m.group(0)) in hay else m.group(0), value)
        if cleaned != value:
            out[field], changed = " ".join(cleaned.split()).strip(" —-:"), True
    if not (str(out.get("title") or "").strip() or str(out.get("body") or "").strip()):
        return None, True
    return out, changed


# A summary is the review's argument in a page or two of prose — the themes,
# the order to tackle them, how the review was made. The cap is a document-size
# bound (the page paints it and every export prints it first), not a style
# rule: 20 000 characters is ~3 000 words, more than any summary needs and small
# enough that a runaway paste cannot bloat review.json.
SUMMARY_MAX_CHARS = 20_000


def clean_summary(value) -> tuple[str | None, str]:
    """Normalise a summary from a request body → ``(text, "")`` or ``(None, error)``.

    An empty string is a valid answer — it clears the summary. Line endings are
    normalised so a Windows browser and the markdown export agree on the bytes.
    """
    if value is None:
        value = ""
    if not isinstance(value, str):
        return None, "summary must be a string"
    text = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text) > SUMMARY_MAX_CHARS:
        return None, f"summary is {len(text)} characters; the cap is {SUMMARY_MAX_CHARS}"
    return text, ""


def next_comment_number(comments) -> int:
    """Next free 1-based ``n`` across a review's existing comments."""
    best = 0
    for c in comments or []:
        n = _num(c.get("n")) if isinstance(c, dict) else None
        if n is not None and n > best:
            best = n
    return int(best) + 1


def data_uri(data: bytes, mime: str = "image/png") -> str:
    """``data:`` URI for embedding a shot in a self-contained export.

    ``render_html_pdf`` uses ``set_content``, so a PDF export cannot fetch an
    image over the network — every screenshot must be inlined or it prints blank.
    """
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def is_own_daemon(cfg, url: str) -> bool:
    """True when ``url`` points at THIS daemon — host AND port must match.

    Matched on host+port rather than "is it private", so a leased sandbox on
    another port, or another machine on the LAN, is not mistaken for us.
    """
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
        # `or` would rewrite an explicit :0 to 80 — 0 is a legal port value.
        port = parts.port if parts.port is not None else (
            443 if parts.scheme == "https" else 80)
    except ValueError:
        return False
    own_host = canonical_hostname(str(getattr(cfg, "host", "") or ""))
    own_port = int(getattr(cfg, "port", 0) or 0)
    host = canonical_hostname(parts.hostname or "")
    if host and own_host and host == own_host and port == own_port:
        return True
    # Loopback is always our own daemon's family; only on the configured port,
    # so a review cannot be pointed at some other local service.
    return host in {"127.0.0.1", "::1", "localhost"} and port == own_port


def authed_url(cfg, url: str) -> str:
    """``url`` with a one-shot sign-in token when it points at this daemon.

    Without this a review of our own pages captures the LOGIN GATE, not the
    page — which is exactly what the first real run produced: eleven anchors
    and four comments about a sign-in button. The browser has no session.

    ``?token=`` is the daemon's deep-link sign-in: it sets the ``eos_session``
    cookie and 302s to the same path with the token stripped, so the credential
    never survives into ``page.url``. ``strip_token`` below is the belt to that
    braces — a token must never reach review.json, and from there an exported
    PDF sitting in the vault.
    """
    from urllib.parse import quote, urlsplit, urlunsplit

    # Guard the ambiguous authority HERE, not only in the caller. urlsplit takes
    # the host after the last '@', so `http://192.168.1.1\@127.0.0.1:9000/` reads
    # as this daemon in Python while a browser's WHATWG parser navigates to
    # 192.168.1.1 — handing our credential to a private host. capture_view calls
    # `_url_refusal` first, but this function is reachable from elsewhere
    # (discovery), and a function that returns a secret must not depend on a
    # check living in a different function.
    authority = url.split("//", 1)[-1].split("/", 1)[0]
    lowered = authority.lower()
    if "\\" in authority or "%5c" in lowered or "@" in authority:
        return url
    if not is_own_daemon(cfg, url):
        return url
    # Loopback only. is_own_daemon also matches cfg.host, which under
    # network.mode = private|public is a LAN address or a public hostname — and
    # a bearer credential in a query string travels in the REQUEST LINE, which
    # every reverse proxy and CDN logs verbatim and which leaks in the Referer
    # of any outbound link on the landing page (CWE-598). The daemon's 302-and-
    # strip happens after the request is already logged, and auth-exempt paths
    # (/static/, /login, an app's public_routes) never reach the strip at all.
    # Declining here degrades visibly — you capture the login gate — which is
    # strictly better than a credential in someone's access.log.
    host = canonical_hostname(urlsplit(url).hostname or "")
    if host not in {"127.0.0.1", "::1", "localhost"}:
        return url
    token = str(getattr(cfg, "auth_token", "") or "")
    if not token:
        return url  # local mode: no auth configured, nothing to present
    try:
        p = urlsplit(url)
    except ValueError:
        return url
    # Splice into the QUERY, never by concatenation. `url + "?token="` puts the
    # credential inside the fragment whenever the URL has one
    # ("/hub/#tab" -> "/hub/#tab?token=..."), and a browser never transmits a
    # fragment — so sign-in silently does not happen and you review the login
    # gate anyway, which is the exact failure this function exists to fix.
    q = f"{p.query}&token={quote(token, safe='')}" if p.query else f"token={quote(token, safe='')}"
    return urlunsplit((p.scheme, p.netloc, p.path, q, p.fragment))


_CRED_KEYS = {"token", "access_token", "auth_token", "password"}


def _drop_creds(qs: str) -> tuple[str, bool]:
    """``(query without credential params, whether one was present)``."""
    from urllib.parse import parse_qsl, urlencode

    pairs = parse_qsl(qs, keep_blank_values=True)
    kept = [(k, v) for k, v in pairs if k.lower() not in _CRED_KEYS]
    if len(kept) == len(pairs):
        return qs, False
    return urlencode(kept), True


def strip_token(url: str) -> str:
    """Remove a sign-in credential from a URL's query or fragment before it is
    recorded. Does NOT touch userinfo or the path — ``_url_refusal`` refuses a
    ``@`` on the capture path, and no caller puts a secret in a path segment.

    Rewrites only the component a credential was actually found in.
    ``urlencode(parse_qsl(...))`` is a LOSSY round-trip — it turns ``?q=a%20b``
    into ``?q=a+b``, ``?flag`` into ``?flag=``, and destroys ``?a=1;b=2`` — so
    applying it unconditionally would make every recorded URL a string that was
    never visited, on external sites where no token can exist.
    """
    from urllib.parse import urlsplit, urlunsplit

    try:
        p = urlsplit(url)
    except ValueError:
        return url
    query, hit_q = _drop_creds(p.query) if p.query else (p.query, False)
    fragment, hit_f = _drop_creds(p.fragment) if p.fragment else (p.fragment, False)
    if not (hit_q or hit_f):
        return url
    return urlunsplit((p.scheme, p.netloc, p.path, query, fragment))


# ── Sources: the shot-producer seam (pure half) ──────────────────────
#
# A review is ordered shots plus normalised-coordinate comments; the only
# medium-specific part is how a *source* becomes shots. These helpers are the
# pure half of that seam — the source vocabulary, the legacy-input translation
# and the path confinement a file-backed source must pass. The producers that
# actually touch the browser or the filesystem live in ``sources.py`` and read
# from here, never the other way round (rule 6: shared.py stays a leaf).

#: Every kind a source may declare. ``pdf`` and ``image`` are produced only
#: when their optional libraries are installed (``emptyos[pdf]``); otherwise
#: they are refused with the install hint before a run starts.
SOURCE_KINDS: tuple[str, ...] = ("web", "document", "pdf", "image")

#: Document sources are ``<root>/<relative path>`` where the first segment is
#: one of these allow-list keys. Anything else is refused before it reaches the
#: filesystem — decided 2026-09-10, see the plan.
DOCUMENT_ROOT_KEYS: tuple[str, ...] = ("docs", "vault", "uploads")

#: File types a document producer will render. Markdown only for now; an HTML
#: file would be served verbatim into the daemon's origin, which is a different
#: trust question than rendering text.
DOCUMENT_SUFFIXES: frozenset[str] = frozenset({".md", ".markdown"})

#: A PDF is rasterised page by page; an image is shown as it is.
PDF_SUFFIXES: frozenset[str] = frozenset({".pdf"})
IMAGE_SUFFIXES: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".webp"})

#: The file-backed kinds and the suffixes each accepts. All three resolve the
#: same ``<root>/<path>`` ref under the same allow-listed roots.
FILE_SUFFIXES: dict[str, frozenset[str]] = {
    "document": DOCUMENT_SUFFIXES,
    "pdf": PDF_SUFFIXES,
    "image": IMAGE_SUFFIXES,
}


def file_kind(name: str) -> str | None:
    """The source kind a file name implies from its suffix, or None."""
    import os.path

    suffix = os.path.splitext(str(name or ""))[1].lower()
    for kind, suffixes in FILE_SUFFIXES.items():
        if suffix in suffixes:
            return kind
    return None


def normalize_sources(inputs: dict) -> list[dict]:
    """The sources a run should produce shots for, from any accepted input shape.

    Precedence: an explicit ``sources`` list wins; else a legacy ``views`` list
    (each ``{url, ...}`` becomes ``kind: web``); else a bare ``url`` is one web
    source. A row with no ``ref``/``url``, or an unknown ``kind``, is dropped —
    the stage reports "nothing to capture" rather than guessing. ``views`` stays
    accepted and undocumented on purpose (decision 3, 2026-09-10).
    """
    inputs = inputs or {}
    out: list[dict] = []
    explicit = inputs.get("sources")
    if isinstance(explicit, list) and explicit:
        for s in explicit:
            if not isinstance(s, dict):
                continue
            kind = str(s.get("kind") or "web").strip().lower()
            ref = str(s.get("ref") or s.get("url") or "").strip()
            if kind not in SOURCE_KINDS or not ref:
                continue
            out.append({
                "kind": kind, "ref": ref,
                "title": str(s.get("title") or "").strip(),
                "selector": str(s.get("selector") or "").strip(),
                "focus": str(s.get("focus") or "").strip(),
            })
        return out
    views = inputs.get("views")
    if isinstance(views, list) and views:
        for v in views:
            if isinstance(v, dict) and v.get("url"):
                # A discovered/approved row round-trips through the approval
                # UI with its kind attached; a hand-written legacy row has none.
                kind = str(v.get("kind") or "web").strip().lower()
                if kind not in SOURCE_KINDS:
                    continue
                out.append({
                    "kind": kind, "ref": str(v["url"]).strip(),
                    "title": str(v.get("title") or "").strip(),
                    "selector": str(v.get("selector") or "").strip(),
                    "focus": str(v.get("focus") or "").strip(),
                })
        return out
    url = str(inputs.get("url") or "").strip()
    if url:
        out.append({"kind": "web", "ref": url, "title": "", "selector": "", "focus": ""})
    return out


def invalid_source_rows(raw) -> str | None:
    """A human reason the caller's ``sources`` list cannot be accepted, or None.

    ``normalize_sources`` *drops* a malformed row so a stored run never carries
    one; this is the loud half, for the request boundary. Without it a request
    naming only unknown kinds normalised to an empty list and quietly became a
    bare-URL discovery — the caller never learned its sources had vanished.
    """
    if raw is None:
        return None
    if not isinstance(raw, list):
        return "sources must be a list of {kind, ref}"
    for i, s in enumerate(raw, 1):
        if not isinstance(s, dict):
            return f"source {i} is not an object"
        kind = str(s.get("kind") or "web").strip().lower()
        if kind not in SOURCE_KINDS:
            return f"source {i}: unknown kind {kind!r} — one of {', '.join(SOURCE_KINDS)}"
        if not str(s.get("ref") or s.get("url") or "").strip():
            return f"source {i}: missing ref"
    return None


def own_daemon_url(cfg, path: str) -> str:
    """The loopback address of one of this daemon's own routes.

    Loopback on the configured port on purpose: ``is_own_daemon`` admits it and
    ``authed_url`` presents the sign-in token only to a loopback host. One
    builder, because two sites disagreed on the fallback port (``or 0`` vs
    ``or 9000``) and ``:0`` passes ``is_own_daemon`` when the port is unset.
    """
    port = int(getattr(cfg, "port", 0) or 9000)
    return f"http://127.0.0.1:{port}/{path.lstrip('/')}"


def split_document_ref(ref: str) -> tuple[str, str] | None:
    """``"docs/spec/05-x.md"`` → ``("docs", "spec/05-x.md")``, or None.

    Only the root key is validated here; the relative half is confined by
    :func:`confine_path` against the real root directory.
    """
    ref = (ref or "").strip().replace("\\", "/").lstrip("/")
    root, _, rel = ref.partition("/")
    if root not in DOCUMENT_ROOT_KEYS or not rel:
        return None
    return root, rel


def confine_path(root, rel: str, *, suffixes=DOCUMENT_SUFFIXES):
    """``root / rel`` when every segment is a plain slug and the result stays
    under ``root``; otherwise ``None``.

    Two independent guards, both needed. The per-segment check refuses ``..``,
    hidden files, trailing dots and Windows reserved device stems *before* any
    path is built (the same rule every caller-supplied id in EmptyOS passes).
    The ``is_relative_to`` check after ``resolve()`` catches what a segment rule
    cannot — a symlink under the root pointing out of it. A suffix outside
    ``suffixes`` is refused so a document source can never serve an arbitrary
    file the root happens to contain.
    """
    from pathlib import Path

    from emptyos.sdk.utils import safe_path_segment

    parts = [p for p in (rel or "").replace("\\", "/").split("/") if p != ""]
    if not parts or any(not safe_path_segment(p) for p in parts):
        return None
    root = Path(root)
    try:
        base = root.resolve()
        candidate = root.joinpath(*parts).resolve()
    except (OSError, RuntimeError):
        return None
    if not candidate.is_relative_to(base) or candidate == base:
        return None
    if suffixes and candidate.suffix.lower() not in suffixes:
        return None
    return candidate


# A requirement-style identifier at the START of a cell or heading:
# ``SR-INP-06``, ``ALG-GEN-FIRM-01``, ``REFUSE-FDR-01``, ``BR-01``, ``O-3``,
# ``AC-B1``, ``E-10``. Uppercase groups joined by hyphens, ending in a number
# (optionally with a letter prefix like the gate-B criteria). The bounds are
# generous on purpose — a bounded class silently drops the entries past it
# (memory: `feedback_bounded_char_class_silently_drops`); the longest group in
# the spec pack is ``REQUIREMENT`` (11) and the longest number ``0100`` (4).
_LEADING_ID_RE = re.compile(
    r"^\s*([A-Z]{1,16}(?:-[A-Z0-9]{1,16})*-(?:\d{1,5}|[A-Z]\d{1,3}))(?![A-Za-z0-9])")
_TAG_RE = re.compile(r"<[^>]+>")
_NBSP = " "


def leading_id(text: str) -> str:
    """The identifier a cell or heading opens with, or ``""``.

    Markup is stripped first (``<code>``, ``<strong>`` wrappers are common in a
    requirements table); a non-breaking space — as a character or as the
    ``&nbsp;`` entity python-markdown may leave in a cell — reads as a space.
    """
    plain = _TAG_RE.sub("", text or "").replace("&nbsp;", " ").replace(_NBSP, " ")
    m = _LEADING_ID_RE.match(plain)
    return m.group(1) if m else ""


def attach_refs(comments: list[dict], anchors) -> list[dict]:
    """Set each comment's ``ref`` — the requirement id its anchored element
    opens with — from the anchor's measured text. ``""`` when the comment has no
    anchor or the element does not open with an id.

    Derived, never typed: the anchor text is what the capture measured on the
    row, so a pin on the `SR-INP-06` row carries `SR-INP-06` whatever the model
    wrote in the title. In place, and returns the list.
    """
    text_by_el = {a.get("el"): a.get("text") or ""
                  for a in (anchors or []) if isinstance(a, dict) and a.get("el")}
    for c in comments:
        c["ref"] = leading_id(text_by_el.get(c.get("el") or "", ""))
    return comments


def is_document_shot(shot: dict) -> bool:
    """True when the shot is a page of a written document — a rendered markdown
    file or a PDF page — where a leading hyphen-number is a requirement id."""
    src = shot.get("source") if isinstance(shot.get("source"), dict) else {}
    return str(src.get("kind") or "") in ("document", "pdf")


def move_pin(comment: dict, x: float, y: float) -> dict:
    """A hand placement of a pin. In place, and returns the comment.

    Moving a pin by hand makes it a human placement — it is no longer the
    measured position, and saying otherwise would launder an opinion as a
    measurement. The element it was measured against, and the requirement
    id derived from that element, go with it: a pin dragged from the
    `SR-INP-06` row to `SR-INP-07` must not keep printing `SR-INP-06`.
    """
    comment["x"] = round(clamp01(x), 5)
    comment["y"] = round(clamp01(y), 5)
    comment["anchor"] = "human"
    comment["el"] = ""
    comment["ref"] = ""
    return comment


#: How far, in PAGE PIXELS, a click may land from the nearest measured element
#: and still snap to it. Past this the click is in empty margin and no anchor
#: can honestly claim it — the comment is then about the view as a whole, never
#: an estimate. 48 px is about two lines of body text at 1200 px, so a click
#: between two table rows always reaches one of them.
SNAP_MAX_PX = 48.0


def _rect_distance(px: float, py: float, rect: tuple) -> float:
    """Euclidean distance from a page point to a box's nearest edge; 0 inside."""
    rx, ry, rw, rh = rect
    dx = max(rx - px, 0.0, px - (rx + rw))
    dy = max(ry - py, 0.0, py - (ry + rh))
    return math.hypot(dx, dy)


def snap_to_anchor(x: float, y: float, anchors, origin, *,
                   max_px: float = SNAP_MAX_PX) -> dict | None:
    """The measured anchor a click at normalised ``(x, y)`` means, or ``None``.

    The click is normalised against the captured ``origin`` region (the same
    frame every pin is stored in); it is converted back to page pixels so the
    distance to each anchor's measured rect is a real length, not a fraction of
    a region whose width and height differ. Chooses the anchor the point is
    *inside* when there is one — the smallest such box, so a click on a table
    row inside a list item picks the row — else the nearest within ``max_px``.
    Nothing within reach → ``None``: the caller must not invent a position.
    """
    o = _box(origin)
    xn, yn = _num(x), _num(y)
    if o is None or xn is None or yn is None:
        return None
    ox, oy, ow, oh = o
    px, py = ox + clamp01(xn) * ow, oy + clamp01(yn) * oh
    best, best_key = None, None
    for a in anchors or []:
        if not isinstance(a, dict) or not a.get("el"):
            continue
        r = _box(a.get("rect"))
        if r is None:
            continue
        d = _rect_distance(px, py, r)
        if d > max_px:
            continue
        key = (d, r[2] * r[3])   # nearest first; inside several → the smallest
        if best_key is None or key < best_key:
            best, best_key = a, key
    return best


def pin_to_anchor(comment: dict, anchor: dict, origin, *, derive_ref: bool = True) -> dict | None:
    """Pin a comment to a measured anchor — the mirror of :func:`move_pin`.

    Position comes from the anchor's rect through the same ``rect_to_point``
    every model-placed pin goes through, so a hand click and a review pass
    cannot disagree about where an element is. ``anchor: dom`` because the
    position IS measured; ``el`` names the element; ``ref`` is the id its text
    opens with — only when ``derive_ref`` (a document shot: the same gate the
    review pass applies through ``is_document_shot``, so a web heading like
    "COVID-19 response" never becomes a requirement id). In place, and returns
    the comment — or ``None`` when the anchor does not intersect the region
    (unreachable for a sidecar anchor, which the capture already filtered
    through this predicate).
    """
    pt = rect_to_point(anchor.get("rect"), origin)
    if pt is None:
        return None
    comment["x"], comment["y"] = round(pt[0], 5), round(pt[1], 5)
    comment["anchor"] = "dom"
    comment["el"] = str(anchor.get("el") or "")
    comment["ref"] = leading_id(str(anchor.get("text") or "")) if derive_ref else ""
    return comment


def ref_prefix(comment: dict) -> str:
    """The comment's ``ref`` as a title prefix — or ``""`` when there is none,
    or when the title already opens with it (any case, with or without a
    leading bracket).

    The requirements prompt asks the model to start titles with the id, and
    the export prints the derived ref before the title; without this the
    first live run printed ``SR-PRJ-04 — SR-PRJ-04 — …``. The ref is printed
    whenever the title does NOT open with it, so a title naming a different id
    still shows the id the pin actually sits on.
    """
    ref = str(comment.get("ref") or "").strip()
    if not ref:
        return ""
    title = str(comment.get("title") or "").lstrip().lstrip("[(").casefold()
    low = ref.casefold()
    if title.startswith(low) and not title[len(low):len(low) + 1].isalnum():
        return ""
    return ref


def source_line(shot: dict) -> str:
    """One line naming where a shot came from, by kind.

    A document shot's ``url`` is the loopback render address — true, and
    useless in a report or a prompt. Its source ref and content hash are what
    a reader can act on. Web shots (and pre-seam shots with no ``source``
    block) keep their URL. A non-web source with no ref prints nothing rather
    than falling back to the address this line exists to hide.
    """
    src = shot.get("source") if isinstance(shot.get("source"), dict) else {}
    kind = str(src.get("kind") or "web")
    if kind == "web":
        url = str(shot.get("url") or src.get("ref") or "")
        return f"Route: {url}" if url else ""
    if not src.get("ref"):
        return ""
    sha = str(src.get("sha256") or "")
    tail = f" (sha256 {sha[:12]})" if sha else ""
    label = {"document": "Document", "pdf": "PDF", "image": "Image"}.get(kind, kind.title())
    page = f", page {src['page']} of {src.get('pages') or '?'}" if src.get("page") else ""
    return f"{label}: {src['ref']}{page}{tail}"


_ROW_RE = re.compile(r"<tr(?P<attrs>\s[^>]*)?>\s*<t[dh][^>]*>(?P<cell>.*?)</t[dh]>", re.S)
_HEADING_RE = re.compile(r"<h(?P<n>[1-6])(?P<attrs>\s[^>]*)?>(?P<body>.*?)</h(?P=n)>", re.S)


def stamp_ids(html: str) -> str:
    """Give every table row and heading that opens with an identifier an
    addressable ``id`` — ``<tr id="SR-INP-06">``, and an empty anchor
    ``<a id="BR-01"></a>`` before a heading (its own slug id, which the table
    of contents links to, is left alone).

    First occurrence wins per document: a traceability matrix repeats ids in
    later rows, and a duplicate ``id`` is invalid HTML that makes the deep link
    ambiguous. A row that already carries an ``id`` is not touched.
    """
    seen: set[str] = set()

    def _row(m: re.Match) -> str:
        attrs = m.group("attrs") or ""
        if re.search(r"\sid=", attrs):
            return m.group(0)
        rid = leading_id(m.group("cell"))
        if not rid or rid in seen:
            return m.group(0)
        seen.add(rid)
        return f'<tr id="{rid}"{attrs}>' + m.group(0)[len("<tr") + len(attrs) + 1:]

    def _heading(m: re.Match) -> str:
        rid = leading_id(m.group("body"))
        if not rid or rid in seen:
            return m.group(0)
        seen.add(rid)
        return f'<a id="{rid}"></a>' + m.group(0)

    html = _ROW_RE.sub(_row, html)
    return _HEADING_RE.sub(_heading, html)


_MERMAID_FENCE_RE = re.compile(r"^```mermaid[ \t]*\n(.*?)^```[ \t]*$", re.S | re.M)


def extract_mermaid(md: str) -> tuple[str, int]:
    """Replace ```` ```mermaid ```` fences with ``<div class="mermaid">`` blocks
    the browser-side renderer draws, and say how many there were (so the page
    shell loads the renderer only when needed). The diagram text is escaped —
    it is untrusted document content going into raw HTML.

    Fences are recognised at column 0 only; an indented fence (inside a list
    item) is left to the markdown renderer and shows as a code block.
    """
    from html import escape

    count = 0

    def _sub(m: re.Match) -> str:
        nonlocal count
        count += 1
        return f'<div class="mermaid">\n{escape(m.group(1))}\n</div>'

    return _MERMAID_FENCE_RE.sub(_sub, md or ""), count


_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


def document_title(md: str, fallback: str) -> str:
    """The first ``# `` heading, or ``fallback``.

    A front-matter block (removed by the shared ``strip_frontmatter``, so the
    boundary rule is the one every vault reader uses) or an HTML comment — of
    any length — may precede the title; prose may not, so a document whose
    first content line is a paragraph has no title.
    """
    from emptyos.frontmatter import strip_frontmatter

    body = _HTML_COMMENT_RE.sub("", strip_frontmatter(md or ""))
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip() or fallback
        if s:
            break
    return fallback
