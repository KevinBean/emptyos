"""Shared helpers for embedding viz/designer HTML artifacts into pages & notes.

Two consumers bake a self-contained HTML artifact into a sandboxed `<iframe>`:
designer (page generation — fills `data-viz` placeholders) and the note
artifact-embed feature (KB / general notes embedding a viz artifact durably).
The escape/iframe/fallback logic and the artifact-path/attribute parsing are
identical, so they live here (CLAUDE.md rule 9 — designer is consumer #1, the
note-embed feature is #2).

Pure functions only — no `self`, no kernel access, no I/O. Consumers import
directly, e.g. `from emptyos.sdk.viz_embed import srcdoc_iframe`.

Two render shapes:
  - srcdoc_iframe(html) — bakes the artifact's bytes inline (self-contained;
    used by designer's page bake + the publish path). Content is escaped so it
    cannot break out of the attribute or close the frame early.
  - the note *view* renders an `<iframe src=...>` to a serve endpoint in JS
    (eos-components.js renderMarkdownWithEmbeds) — no Python iframe needed there.

Plus the body-marker grammar (embed_marker / parse_embed_markers) shared by the
bake (writes the marker into the note body) and any server-side consumer.
"""

from __future__ import annotations

import html as _html
import re

# ── Artifact-path + attribute helpers (lifted verbatim from designer/embed.py) ──

# Attribute parser for HTML element attrs (quoted values), e.g. data-viz="chart".
_ATTR_RE = re.compile(r'([\w-]+)\s*=\s*"([^"]*)"' r"|([\w-]+)\s*=\s*'([^']*)'")

# key=value tokens WITHOUT quotes — used by the embed marker grammar where values
# are safe tokens (hex ids, simple words), never free text.
_KV_RE = re.compile(r"([\w-]+)=(\S+)")


def id_from_artifact_path(path: str) -> str:
    """A viz path `.../<base>/outputs/<id>/scene.html` → return `<id>`."""
    parts = (path or "").replace("\\", "/").rstrip("/").split("/")
    return parts[-2] if len(parts) >= 2 else ""


def parse_html_attrs(raw: str) -> dict:
    """Parse quoted HTML attributes from a tag's attribute string → dict."""
    out: dict = {}
    for m in _ATTR_RE.finditer(raw or ""):
        if m.group(1) is not None:
            out[m.group(1).lower()] = m.group(2)
        elif m.group(3) is not None:
            out[m.group(3).lower()] = m.group(4)
    return out


def px_height(height, default: int = 360) -> str:
    """Normalize a height value to a CSS length string. Idempotent on `360px`."""
    h = str(height or "").strip()
    if not h:
        return f"{default}px"
    if h.isdigit():
        return f"{h}px"
    if h.endswith(("px", "vh", "%", "rem", "em")):
        return h
    return f"{default}px"


# ── iframe rendering ──

def srcdoc_iframe(
    content: str,
    *,
    height="360px",
    title: str = "",
    sandbox: str = "allow-scripts",
    extra_style: str = "",
) -> str:
    """Bake a complete HTML artifact into a sandboxed `<iframe srcdoc>`.

    The artifact's HTML is escaped (incl. quotes) so a literal `"` or
    `</iframe>` inside the content cannot break out of the attribute or close
    the frame early — this is the load-bearing safety property. `sandbox` is
    `allow-scripts` only by default: an opaque-origin frame whose scripts run
    but cannot reach the parent DOM, cookies, or storage. **Never** add
    `allow-same-origin`.
    """
    h = px_height(height)
    srcdoc = _html.escape(content or "", quote=True)
    style = (
        f"width:100%;height:{h};border:0;border-radius:12px;"
        f"overflow:hidden;display:block;{extra_style}"
    )
    return (
        f'<iframe srcdoc="{srcdoc}" loading="lazy" '
        f'title="{_html.escape(title)}" '
        f'style="{style}" sandbox="{_html.escape(sandbox, quote=True)}"></iframe>'
    )


def fallback_block(shape: str, brief: str, note: str) -> str:
    """A styled stand-in when an embed can't be produced — never leaves the
    page broken or with a hand-written chart."""
    return (
        '<div style="display:flex;flex-direction:column;align-items:center;'
        "justify-content:center;gap:6px;min-height:160px;padding:24px;"
        "border:1px dashed var(--border,#c9cdd6);border-radius:12px;"
        'color:#888;font:13px/1.5 system-ui,sans-serif;text-align:center;">'
        f"<strong>{_html.escape(shape or 'element')}</strong>"
        f"<span>{_html.escape(brief or '')}</span>"
        f'<span style="font-size:11px;opacity:.7;">{_html.escape(note)}</span>'
        "</div>"
    )


# ── Body-marker grammar (note artifact-embed feature) ──
#
# A marker is a positional anchor in a note's prose body: the prose renders
# normally and the embed iframe is stitched in where the marker sits. Values are
# bake-authored safe tokens (hex ids, simple words) — never user free-text — so
# the unquoted key=value form is safe.

_MARKER_RE = re.compile(r"<!--\s*eos:viz-embed\s+(.*?)-->")


def embed_marker(embed_id: str, *, mode: str = "snapshot", shape: str = "") -> str:
    """Build a body marker for one embed."""
    return f"<!-- eos:viz-embed embed_id={embed_id} mode={mode} shape={shape or ''} -->"


def parse_embed_markers(text: str) -> list[dict]:
    """Return `[{embed_id, mode, shape, raw}]` for every marker in `text`, in
    document order. `raw` is the full marker substring (for split/replace)."""
    out: list[dict] = []
    for m in _MARKER_RE.finditer(text or ""):
        kv = {km.group(1): km.group(2) for km in _KV_RE.finditer(m.group(1) or "")}
        eid = kv.get("embed_id") or ""
        if not eid:
            continue
        out.append({
            "embed_id": eid,
            "mode": kv.get("mode") or "snapshot",
            "shape": kv.get("shape") or "",
            "raw": m.group(0),
        })
    return out
