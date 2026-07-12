"""publish — in-app asset studio: audit + generate diagrams + capture screenshots.

Extracted from app.py to keep the core spine atomic (P4 Atomic, CLAUDE.md
rule 4). Owns: the "referenced media that doesn't exist yet" audit for a post,
plus two generators that fill the gaps from within the app — diagrams
(think -> SVG -> reuse the site rasterizer) and screenshots (the browse
capability + a redaction gate) — each through propose -> preview -> confirm
before anything is written to images/ or media/. The daemon never captures the
real vault unattended: generation is author-triggered and every write is
human-confirmed (.claude/rules/proposed-action.md).

Reaches into other modules: uses the class's _images_dir + _vault_dir +
_active_site/_get_site (defined in media.py / app.py) via ``self``; does NOT
import them. Constants + pure helpers live here with their consumer (rule 5).
Do not import from ``.app`` (it imports us, which would cycle).

Gated by [apps.publish] feature.asset-studio.enabled (default dark) — off ⇒
every route returns {"error": "asset studio disabled"} and nothing changes.
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from emptyos.sdk import web_route
from emptyos.sdk.svg_raster import rasterize_svgs

if TYPE_CHECKING:  # pragma: no cover
    from .app import PublishApp  # noqa: F401 — type hints only


# ─── Bind to PublishApp class as ─────────────────────────────────────
#   _asset_studio_enabled = _assets._asset_studio_enabled
#   _media_dir            = _assets._media_dir
#   _classify_ref         = _assets._classify_ref          # @staticmethod
#   _parse_media_refs     = _assets._parse_media_refs       # @staticmethod
#   _redaction_patterns   = _assets._redaction_patterns
#   _redaction_hits       = _assets._redaction_hits
#   api_assets_status     = _assets.api_assets_status
#   api_assets_audit      = _assets.api_assets_audit
#   api_assets_diagram_propose  = _assets.api_assets_diagram_propose
#   api_assets_diagram_apply    = _assets.api_assets_diagram_apply
#   api_assets_shot_propose     = _assets.api_assets_shot_propose
#   api_assets_shot_apply       = _assets.api_assets_shot_apply
#   api_assets_reject     = _assets.api_assets_reject
#   api_assets_preview    = _assets.api_assets_preview
# Adding a method here? Add a matching binding line in app.py.
# ─────────────────────────────────────────────────────────────────────

ASSET_IMG_RE = re.compile(
    r"!\[[^\]]*\]\(([^)]+\.(?:png|jpg|jpeg|gif|svg|webp))\)", re.IGNORECASE
)

DIAGRAM_SYSTEM = (
    "You generate ONE clean, self-contained SVG technical diagram for a blog "
    "article. Output ONLY the SVG markup — no prose, no markdown fences, no "
    "<html> wrapper. Requirements:\n"
    "- Root <svg> with an explicit viewBox and width/height (landscape, ~1280x720).\n"
    "- Legible text (>=14px), high contrast, a restrained palette (2-3 accent "
    "colours + neutral ink on a light background). No external fonts, images, or "
    "scripts — everything inline and static.\n"
    "- Label the parts clearly; prefer boxes + arrows + short captions over "
    "decoration. It must read as an explanatory figure, not clip-art.\n"
    "Do NOT include personal data, brand names, or placeholder lorem ipsum."
)

_PENDING_SUBDIR = "asset-pending"


def _asset_studio_enabled(self) -> bool:
    return bool(self.app_config("feature.asset-studio.enabled", False))


def _media_dir(self, site: dict | None = None) -> Path:
    """Sibling of _images_dir (media.py) for the site's media/ folder."""
    s = site or self._active_site()
    return Path(self._vault_dir()) / (s.get("source_folder") or "") / "media"


def _classify_ref(raw: str) -> tuple[str, str]:
    """(kind, relative_name) for one image ref. kind ∈ images|media|external.

    Bare filename → images/ (the site convention). Query/anchor stripped.
    """
    raw = (raw or "").split("?")[0].split("#")[0].strip()
    low = raw.lower()
    if low.startswith(("http://", "https://", "data:")):
        return ("external", raw)
    r = raw.lstrip("./")
    while r.startswith("../"):
        r = r[3:]
    if r.startswith("media/"):
        return ("media", r[len("media/"):])
    if r.startswith("images/"):
        return ("images", r[len("images/"):])
    return ("images", r)  # bare → images/ (diagram home)


def _parse_media_refs(body: str) -> list[dict]:
    """Pure: every local image ref in a post body → {ref, kind, name}.

    External (http/data) refs are dropped. Order-preserving, deduped by
    (kind, name). No filesystem access — resolution/status is the caller's job.
    """
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for m in ASSET_IMG_RE.finditer(body or ""):
        raw = m.group(1).strip()
        kind, name = _classify_ref(raw)
        if kind == "external":
            continue
        key = (kind, name)
        if key in seen:
            continue
        seen.add(key)
        out.append({"ref": raw, "kind": kind, "name": name})
    return out


def _repo_root(self) -> Path:
    return Path(self.kernel.config.path).parent


def _redaction_patterns(self) -> list[re.Pattern]:
    """Load .eos-personal + .eos-branding regexes from the repo root.

    Same sets scripts/check-personal.py / check-branding.py use — this is the
    pixel-side gate the eos-screenshot skill applies, ported in-app.
    """
    pats: list[re.Pattern] = []
    root = _repo_root(self)
    for fn in (".eos-personal", ".eos-branding"):
        fp = root / fn
        if not fp.exists():
            continue
        for line in fp.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                pats.append(re.compile(line))
            except re.error:
                pass
    return pats


def _redaction_hits(self, visible_text: str, blurred_text: str = "") -> list[dict]:
    """Pattern hits in visible_text that are NOT covered by a blurred region."""
    bl = (blurred_text or "").lower()
    hits: list[dict] = []
    for p in _redaction_patterns(self):
        for m in p.finditer(visible_text or ""):
            snip = m.group(0)
            if snip.lower() not in bl:
                hits.append({"pattern": p.pattern, "match": snip})
    return hits


def _pending_dir(self, pid: str) -> Path:
    return self.data_subdir(_PENDING_SUBDIR, pid)


def _safe_name(name: str, default_ext: str) -> str:
    """Slug-ish filename with the expected extension, path-traversal-safe."""
    base = re.sub(r"[^a-zA-Z0-9._-]+", "-", (name or "").strip()).strip("-.") or "asset"
    base = base.replace("/", "-").replace("\\", "-")
    if "." not in base:
        base += default_ext
    return base


# ── Status + audit ───────────────────────────────────────────────────

@web_route("GET", "/api/assets/status")
async def api_assets_status(self, request):
    """Cheap feature-detect for the UI: should the 🎨 Assets button show?"""
    return {"enabled": _asset_studio_enabled(self)}


@web_route("GET", "/api/assets/audit")
async def api_assets_audit(self, request):
    """List a post's image refs + whether each resolves to a file.

    Query: post=<filename under posts/> (required), site_id= optional.
    Missing `images/` refs are diagram-shaped; missing `media/` refs are
    screenshot-shaped.
    """
    if not _asset_studio_enabled(self):
        return {"error": "asset studio disabled"}
    post = (request.query_params.get("post") or "").strip()
    if not post or "/" in post or "\\" in post:
        return {"error": "post filename required"}
    site_id = request.query_params.get("site_id", "")
    site = self._get_site(site_id) if site_id else self._active_site()
    if not site:
        return {"error": "no site"}
    source = Path(self._vault_dir()) / (site.get("source_folder") or "")
    post_path = source / "posts" / post
    if not post_path.exists():
        return {"error": f"post not found: {post}"}
    body = post_path.read_text(encoding="utf-8", errors="replace")
    images_dir = self._images_dir(site)
    media_dir = _media_dir(self, site)
    items = []
    for ref in _parse_media_refs(body):
        base = images_dir if ref["kind"] == "images" else media_dir
        target = base / ref["name"]
        items.append({
            **ref,
            "status": "ok" if target.exists() else "missing",
            "generator": "diagram" if ref["kind"] == "images" else "screenshot",
        })
    return {
        "post": post,
        "site": site["id"],
        "items": items,
        "missing": sum(1 for i in items if i["status"] == "missing"),
    }


# ── Diagram generation (think → SVG → rasterize) ──────────────────────

@web_route("POST", "/api/assets/diagram/propose")
async def api_assets_diagram_propose(self, request):
    """Generate an SVG diagram from a prompt; stage it for review (no write to
    images/ yet). Body: {prompt, name?}. Returns {pending_id, preview, name}.
    """
    if not _asset_studio_enabled(self):
        return {"error": "asset studio disabled"}
    data = await self.safe_json(request)
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return {"error": "prompt required"}
    name = _safe_name(data.get("name") or "", ".svg")
    if not name.lower().endswith(".svg"):
        name = name.rsplit(".", 1)[0] + ".svg"
    try:
        raw = await self.think(
            f"Diagram brief:\n{prompt}", domain="code", system=DIAGRAM_SYSTEM
        )
    except Exception as e:
        return {"error": f"generation failed: {e}"}
    svg = _extract_svg(raw)
    if not svg:
        return {"error": "model did not return valid SVG"}
    pid = uuid.uuid4().hex[:12]
    pdir = _pending_dir(self, pid)
    svg_path = pdir / "diagram.svg"
    svg_path.write_text(svg, encoding="utf-8")
    # Rasterize a preview PNG (also the PNG we ship on apply).
    import asyncio
    try:
        await asyncio.to_thread(rasterize_svgs, [svg_path])
    except Exception as e:
        return {"error": f"rasterize failed (playwright?): {e}"}
    png = svg_path.with_suffix(".png")
    if not png.exists():
        return {"error": "rasterize produced no PNG"}
    shutil.copyfile(png, pdir / "preview.png")
    (pdir / "meta.json").write_text(
        json.dumps({"kind": "diagram", "name": name, "prompt": prompt}), encoding="utf-8"
    )
    return {"pending_id": pid, "name": name,
            "preview": f"/publish/api/assets/pending/{pid}/preview.png"}


@web_route("POST", "/api/assets/diagram/apply")
async def api_assets_diagram_apply(self, request):
    """Commit a staged diagram: copy svg + png into the site's images/.
    Body: {pending_id, name?, site_id?}. Returns {ok, written:[...]}.
    """
    if not _asset_studio_enabled(self):
        return {"error": "asset studio disabled"}
    data = await self.safe_json(request)
    pid = (data.get("pending_id") or "").strip()
    pdir = _pending_dir(self, pid) if pid else None
    if not pid or not pdir or not (pdir / "meta.json").exists():
        return {"error": "unknown pending_id"}
    meta = json.loads((pdir / "meta.json").read_text(encoding="utf-8"))
    if meta.get("kind") != "diagram":
        return {"error": "pending is not a diagram"}
    site = self._get_site(data.get("site_id", "")) if data.get("site_id") else self._active_site()
    if not site:
        return {"error": "no site"}
    name = _safe_name(data.get("name") or meta.get("name") or "diagram.svg", ".svg")
    if not name.lower().endswith(".svg"):
        name = name.rsplit(".", 1)[0] + ".svg"
    images_dir = self._images_dir(site)
    images_dir.mkdir(parents=True, exist_ok=True)
    svg_dst = images_dir / name
    png_dst = svg_dst.with_suffix(".png")
    shutil.copyfile(pdir / "diagram.svg", svg_dst)
    shutil.copyfile(pdir / "diagram.png", png_dst)
    shutil.rmtree(pdir, ignore_errors=True)
    await self.emit("publish:asset_created", {"kind": "diagram", "name": name, "site": site["id"]})
    return {"ok": True, "written": [f"images/{svg_dst.name}", f"images/{png_dst.name}"]}


# ── Screenshot capture (browse + redaction gate) ──────────────────────

@web_route("POST", "/api/assets/shot/propose")
async def api_assets_shot_propose(self, request):
    """Capture a screenshot; stage it + run the redaction gate (no write to
    media/ yet). Body: {url, selector?, blur?[list|csv], full_page?}.
    Returns {pending_id, preview, redaction_hits:[...]}.
    """
    if not _asset_studio_enabled(self):
        return {"error": "asset studio disabled"}
    data = await self.safe_json(request)
    url = (data.get("url") or "").strip()
    if not url:
        return {"error": "url required"}
    url = _authed_url(self, url)
    selector = (data.get("selector") or "").strip() or None
    full_page = bool(data.get("full_page"))
    blur = data.get("blur") or []
    if isinstance(blur, str):
        blur = [b.strip() for b in blur.split(",") if b.strip()]
    blur = [b for b in blur if isinstance(b, str) and b.strip()]

    pid = uuid.uuid4().hex[:12]
    pdir = _pending_dir(self, pid)
    ctx = f"asset-{pid}"
    ok, detail = await self.try_browse("navigate", url=url, context_id=ctx)
    if not ok:
        return {"error": f"navigate failed: {detail}"}
    if blur:
        js = (
            "(function(){var S=" + json.dumps(blur) + ";"
            "S.forEach(function(sel){try{document.querySelectorAll(sel)"
            ".forEach(function(e){e.style.filter='blur(11px)';});}catch(_){} });})()"
        )
        await self.try_browse("eval", expression=js, context_id=ctx)
    # Redaction scan: full visible text minus text inside blurred regions.
    _, snap = await self.try_browse("snapshot", context_id=ctx)
    visible = (snap or {}).get("text", "") if isinstance(snap, dict) else ""
    blurred_text = ""
    for sel in blur:
        _, s = await self.try_browse("snapshot", selector=sel, context_id=ctx)
        if isinstance(s, dict):
            blurred_text += "\n" + (s.get("text") or "")
    hits = _redaction_hits(self, visible, blurred_text)
    shot_path = pdir / "preview.png"
    sok, sdetail = await self.try_browse(
        "screenshot", path=str(shot_path), selector=selector,
        full_page=full_page, context_id=ctx,
    )
    await self.try_browse("close", context_id=ctx)
    if not sok or not shot_path.exists():
        return {"error": f"screenshot failed: {sdetail}"}
    (pdir / "meta.json").write_text(
        json.dumps({"kind": "screenshot", "url": data.get("url"), "blur": blur,
                    "redaction_hits": hits}), encoding="utf-8"
    )
    return {"pending_id": pid, "redaction_hits": hits,
            "preview": f"/publish/api/assets/pending/{pid}/preview.png"}


@web_route("POST", "/api/assets/shot/apply")
async def api_assets_shot_apply(self, request):
    """Commit a staged screenshot to the site's media/. Body: {pending_id,
    name, alt?, site_id?, force?}. Refuses when the redaction gate flagged a
    leak unless force=true. Returns {ok, written:[...]}.
    """
    if not _asset_studio_enabled(self):
        return {"error": "asset studio disabled"}
    data = await self.safe_json(request)
    pid = (data.get("pending_id") or "").strip()
    pdir = _pending_dir(self, pid) if pid else None
    if not pid or not pdir or not (pdir / "meta.json").exists():
        return {"error": "unknown pending_id"}
    meta = json.loads((pdir / "meta.json").read_text(encoding="utf-8"))
    if meta.get("kind") != "screenshot":
        return {"error": "pending is not a screenshot"}
    if meta.get("redaction_hits") and not data.get("force"):
        return {"error": "redaction gate: visible text matched protected patterns",
                "redaction_hits": meta["redaction_hits"]}
    site = self._get_site(data.get("site_id", "")) if data.get("site_id") else self._active_site()
    if not site:
        return {"error": "no site"}
    name = _safe_name(data.get("name") or "screenshot.png", ".png")
    if not name.lower().endswith(".png"):
        name = name.rsplit(".", 1)[0] + ".png"
    media_dir = _media_dir(self, site)
    media_dir.mkdir(parents=True, exist_ok=True)
    dst = media_dir / name
    shutil.copyfile(pdir / "preview.png", dst)
    written = [f"media/{dst.name}"]
    alt = (data.get("alt") or "").strip()
    if alt:
        (dst.with_suffix(".alt.txt")).write_text(alt, encoding="utf-8")
        written.append(f"media/{dst.stem}.alt.txt")
    shutil.rmtree(pdir, ignore_errors=True)
    await self.emit("publish:asset_created", {"kind": "screenshot", "name": name, "site": site["id"]})
    return {"ok": True, "written": written}


# ── Shared: reject + preview serving ──────────────────────────────────

@web_route("POST", "/api/assets/reject")
async def api_assets_reject(self, request):
    """Discard a staged asset. Body: {pending_id}."""
    if not _asset_studio_enabled(self):
        return {"error": "asset studio disabled"}
    data = await self.safe_json(request)
    pid = (data.get("pending_id") or "").strip()
    if not pid:
        return {"error": "pending_id required"}
    pdir = _pending_dir(self, pid)
    shutil.rmtree(pdir, ignore_errors=True)
    return {"ok": True}


@web_route("GET", "/api/assets/pending/{pid}/preview.png")
async def api_assets_preview(self, request):
    """Serve a staged asset's preview PNG (path-traversal-safe)."""
    if not _asset_studio_enabled(self):
        return {"error": "asset studio disabled"}
    pid = request.path_params.get("pid", "")
    return self.serve_data_file(_PENDING_SUBDIR, pid, "preview.png", media_type="image/png")


# ── Module-local pure helpers ─────────────────────────────────────────

def _extract_svg(raw: str) -> str:
    """Pull the <svg>…</svg> block out of an LLM reply (fences/preamble safe)."""
    if not raw:
        return ""
    lo = raw.find("<svg")
    hi = raw.rfind("</svg>")
    if lo == -1 or hi == -1 or hi < lo:
        return ""
    return raw[lo:hi + len("</svg>")].strip()


def _authed_url(self, url: str) -> str:
    """Append the daemon's auth token for a same-host private-mode URL so a
    headless capture of our own pages isn't bounced to the login screen.
    External URLs are returned unchanged.

    The loopback check is anchored on the parsed HOSTNAME (exact match), not
    a substring of the whole URL — `https://evil.com/?x=localhost` or a host
    like `127.0.0.1.evil.com` must never receive the token (the capture
    would exfiltrate it to that host).
    """
    try:
        from urllib.parse import parse_qsl, urlparse

        p = urlparse(url)
        host = (p.hostname or "").lower().rstrip(".")
        if host not in ("127.0.0.1", "::1", "localhost"):
            return url
        if any(k == "token" for k, _ in parse_qsl(p.query, keep_blank_values=True)):
            return url
        tok = getattr(self.kernel.config, "auth_token", "") or ""
        if not tok:
            return url
        sep = "&" if p.query else "?"
        return f"{url}{sep}token={tok}"
    except Exception:
        return url
