"""markitup — visual review of a captured surface.

Point it at a page — a client's site or one of our own — and it captures the
views worth reviewing, drafts tagged comments against real page elements, and
pins each comment to the exact region it is about. The daemon page is the
working artifact; markdown and PDF are exports.

The spine holds the storage shape, the CRUD surface, and the run entry points.
Everything with weight lives in a helper module (see the binding block below and
`.claude/rules/multi-module-apps.md`).

**A review is a run.** It lives in ``self.runs("reviews")`` — the same registry
the pipeline writes to — with ``run.json`` holding pipeline state and
``review.json`` the document every surface reads, plus ``shots/``. Keeping them
in one directory is what lets the hub panel surface a paused run and the review
list surface a finished one without two stores that can disagree.
"""

from __future__ import annotations

import time
from typing import Any

from emptyos.sdk import BaseApp, web_route
from emptyos.sdk.utils import path_segment_error, safe_path_segment

from . import capture as _capture
from . import discovery as _discovery
from . import exporting as _exporting
from . import review as _review
from . import sources as _sources
from . import stages as _stages
from . import vision as _vision
from .rubrics import RUBRICS, get_rubric, normalize_tag, rubric_ids
from .shared import (
    clamp01,
    clean_summary,
    invalid_source_rows,
    move_pin,
    next_comment_number,
    normalize_sources,
    own_daemon_url,
    strip_token,
)

REVIEW_DOC = "review.json"
# A rebuilt version of the reviewed surface, dropped in the review directory by
# hand. Fixed name so no part of the served path is caller-controlled.
PROPOSAL_NAME = "proposal.html"
# A self-contained page inlines its own CSS/JS/images, so it is bigger than
# source but nowhere near this. The cap exists so an upload cannot fill the
# disk, not to police the design.
PROPOSAL_MAX_BYTES = 2 * 1024 * 1024


class MarkitupApp(BaseApp):
    """Visual review app. See the module docstring for the storage shape."""

    # ── Bindings (see each helper module's banner) ──
    # capture.py
    # is_own_daemon / authed_url are deliberately NOT bound. authed_url returns
    # a URL carrying this daemon's auth_token, and POST /api/apps/{id}/rpc/{m}
    # dispatches any non-underscore attribute on the instance with JSON kwargs.
    # Binding it would publish a token oracle that hands the raw credential to
    # same-origin JS — defeating the httponly session cookie, which exists so
    # page script cannot read the credential. Both are called module-locally.
    _url_refusal = _capture._url_refusal
    capture_view = _capture.capture_view
    # discovery.py
    propose_views = _discovery.propose_views
    # sources.py — the shot-producer seam
    document_roots = _sources.document_roots
    resolve_document = _sources.resolve_document
    render_document = _sources.render_document
    api_render = _sources.api_render
    source_refusal = _sources.source_refusal
    produce_shots = _sources.produce_shots
    # review.py
    review_system = _review.review_system
    review_shot = _review.review_shot
    # vision.py
    place_by_vision = _vision.place_by_vision
    # stages.py
    build_pipeline = _stages.build_pipeline
    make_budget = _stages.make_budget
    # exporting.py
    export_markdown = _exporting.export_markdown
    export_pdf = _exporting.export_pdf

    # ── storage ──────────────────────────────────────────────────────

    def _registry(self):
        return self.runs("reviews")

    def read_review(self, rid: str) -> dict | None:
        """The review document, or None. Safe against a traversal-shaped id."""
        if not safe_path_segment(rid):
            return None
        handle = self._registry().get(rid)
        return handle.read_state(name=REVIEW_DOC) if handle else None

    def _save_review(self, rid: str, doc: dict) -> None:
        handle = self._registry().get(rid)
        if handle is None:
            raise ValueError(f"no review with id {rid!r}")
        doc["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        handle.write_state(doc, name=REVIEW_DOC)

    def review_dir(self, rid: str):
        handle = self._registry().get(rid) if safe_path_segment(rid) else None
        return handle.dir if handle else None

    def list_reviews(self, limit: int = 50) -> list[dict]:
        """Saved reviews, newest first — the ``markitup.list_reviews`` verb.

        Summary rows only. A review's comments can run to dozens and no caller
        of a list needs them.
        """
        out = []
        for handle in self._registry().recent(limit):
            doc = handle.read_state(name=REVIEW_DOC)
            if doc:
                comments = doc.get("comments") or []
                out.append({
                    "id": doc.get("id") or handle.run_id,
                    "title": doc.get("title") or handle.run_id,
                    "source": doc.get("source", ""),
                    "rubric": doc.get("rubric", ""),
                    "shots": len(doc.get("shots") or []),
                    "comments": len(comments),
                    "open": sum(1 for c in comments if c.get("status") == "open"),
                    "created": doc.get("created", ""),
                    "updated": doc.get("updated", ""),
                    "status": "ready",
                })
                continue
            # A run that has not reached `assemble` has no review.json — which
            # is the NORMAL state of a run waiting at the approval gate, not an
            # error. Skipping those made the app's headline feature unreachable:
            # the run was absent from the list and its detail view 404-ed, so
            # the shot list could only be approved by hand-POSTing to the API.
            state = handle.read_state()
            if not state:
                continue
            inputs = state.get("inputs") or {}
            first_src = next((s.get("ref", "") for s in (inputs.get("sources") or [])
                              if isinstance(s, dict)), "")
            out.append({
                "id": handle.run_id,
                "title": (inputs.get("title") or inputs.get("url") or first_src
                          or handle.run_id),
                "source": inputs.get("url", "") or first_src,
                "rubric": inputs.get("rubric", ""),
                "shots": 0, "comments": 0, "open": 0,
                "created": inputs.get("_now", ""),
                "updated": "",
                "status": state.get("status", "unknown"),
                "stage": state.get("stage") or "",
                "error": state.get("error") or "",
            })
        return out

    # ── read surface ─────────────────────────────────────────────────

    @web_route("GET", "/api/rubrics")
    async def api_rubrics(self, request) -> dict:
        """The rubric table — the UI derives its filter chips and pin colours
        from exactly this, so a tag can never render with a colour the backend
        does not know about."""
        return {
            "rubrics": [
                {"id": rid, "label": r["label"], "hint": r["hint"], "tags": r["tags"]}
                for rid, r in ((x, get_rubric(x)) for x in rubric_ids())
            ],
            "default": self.setting_or_config("markitup.rubric", "site"),
        }

    @web_route("GET", "/api/reviews")
    async def api_list(self, request) -> dict:
        return {"reviews": self.list_reviews()}

    @web_route("GET", "/api/reviews/{rid}")
    async def api_get(self, request) -> dict:
        rid = request.path_params.get("rid", "")
        err = path_segment_error(rid, "review id")
        if err:
            return {"error": err}
        doc = self.read_review(rid)
        if not doc:
            return {"error": f"no review with id {rid!r}"}
        # Sibling field, not a key on the document: whether a proposal exists is
        # a fact about the directory, and writing it into review.json would give
        # it a second home that can disagree with the filesystem.
        d = self.review_dir(rid)
        return {"review": doc,
                "has_proposal": bool(d and (d / PROPOSAL_NAME).exists())}

    @web_route("GET", "/api/reviews/{rid}/shots/{name}")
    async def api_shot(self, request):
        """Serve one screenshot.

        Both path segments are guarded: the id and the filename each reach the
        filesystem, and either could otherwise walk out of the run directory.
        """
        from starlette.responses import JSONResponse

        rid = request.path_params.get("rid", "")
        name = request.path_params.get("name", "")
        for raw, label in ((rid, "review id"), (name, "shot name")):
            err = path_segment_error(raw, label)
            if err:
                return JSONResponse({"error": err}, status_code=400)
        if not name.endswith(".png"):
            # The docstring says "one screenshot". Interior dots are legal in a
            # path segment, so without this the .anchors.json sidecar served as
            # image/png.
            return JSONResponse({"error": "not a screenshot"}, status_code=404)
        d = self.review_dir(rid)
        png = (d / "shots" / name) if d else None
        if not png or not png.exists():
            return JSONResponse({"error": "no such shot"}, status_code=404)

        from starlette.responses import FileResponse

        return FileResponse(str(png), media_type="image/png")

    @web_route("GET", "/api/reviews/{rid}/proposal")
    async def api_proposal(self, request):
        """Serve this review's proposal page — a rebuilt version of the surface
        the review is about.

        **The URL is the point, not the file.** ``_url_refusal`` rejects
        ``file://`` outright and rejects every non-public host that is not this
        daemon, so a rebuilt page on disk — or one served from the external-lab
        host on :9100 — cannot be captured no matter how good it is. Served from
        here it lands on the ``is_own_daemon`` carve-out and ``authed_url``
        signs the request, which is what lets a review be re-run against the
        thing it asked for. Without this route the loop stops at "here is a
        proposal" and nothing ever checks it.

        The proposal must be **self-contained**: only this one file is served,
        so a relative ``<script src>`` or ``<img>`` resolves against a path that
        holds nothing. Inline everything.

        Nothing about the path is caller-controlled beyond ``rid`` — the
        filename is fixed, so unlike ``api_shot`` there is no second segment to
        guard.
        """
        from starlette.responses import FileResponse, JSONResponse

        rid = request.path_params.get("rid", "")
        err = path_segment_error(rid, "review id")
        if err:
            return JSONResponse({"error": err}, status_code=400)
        d = self.review_dir(rid)
        page = (d / PROPOSAL_NAME) if d else None
        if not page or not page.exists():
            return JSONResponse(
                {"error": f"no proposal for {rid!r} — put one at {PROPOSAL_NAME} "
                          f"in the review directory"},
                status_code=404)
        # text/html on purpose: this is served to be RENDERED and captured, not
        # downloaded. The viewport middleware rewrites every text/html response
        # (.claude/rules/dev-gotchas.md), so the bytes on the wire differ
        # slightly from the file — correct here, since what gets reviewed is the
        # rendered page, but it means this route is not a way to verify the
        # file's contents.
        #
        # The CSP is load-bearing, not defence in depth. This content arrives
        # over HTTP (see api_put_proposal) and is served from OUR origin, so
        # without it a script in a proposal would run with the daemon's
        # authority and the browser would attach the session cookie to anything
        # it fetched. `sandbox allow-scripts` gives the document an opaque
        # origin while still rendering and screenshotting — measured: it keeps
        # scripts working (the proposal's own toggle) but `document.cookie` and
        # `localStorage` both raise SecurityError.
        return FileResponse(str(page), media_type="text/html", headers={
            "Content-Security-Policy": "sandbox allow-scripts",
            "X-Content-Type-Options": "nosniff",
        })

    @web_route("POST", "/api/reviews/{rid}/proposal")
    async def api_put_proposal(self, request) -> dict:
        """Store a rebuilt version of the reviewed surface.

        Body: ``{"html": "<!doctype html>…"}``. One file per review, replacing
        any previous one — a proposal is the current answer to a review, not a
        history (the review's own exports are the record).

        Must stay self-contained; only this file is served, so a relative
        ``<script src>`` resolves to nothing. Inline everything.
        """
        rid = request.path_params.get("rid", "")
        err = path_segment_error(rid, "review id")
        if err:
            return {"error": err}
        d = self.review_dir(rid)
        if not d or not d.exists():
            return {"error": f"no review with id {rid!r}"}
        try:
            body = await request.json()
        except Exception:
            return {"error": "body must be JSON"}
        html = body.get("html")
        if not isinstance(html, str) or not html.strip():
            return {"error": "html must be a non-empty string"}
        size = len(html.encode("utf-8"))
        if size > PROPOSAL_MAX_BYTES:
            return {"error": f"proposal is {size} bytes; the cap is "
                             f"{PROPOSAL_MAX_BYTES}"}
        # Through the capability, not atomic_write_text directly — CLAUDE.md
        # rule 1, the same reason export_markdown states beside its own write.
        # `atomic=True` is not decoration: it makes the provider run the fsync
        # on a worker thread, where calling the sync helper from this handler
        # would block the event loop mid-write (.claude/rules/dev-gotchas.md's
        # "sync call in async context"). Atomic because a half-written page
        # would still serve and still capture, so a torn upload becomes a
        # plausible-looking review of a broken document.
        await self.write(str(d / PROPOSAL_NAME), html, atomic=True)
        await self.emit("markitup:proposal_saved", {"id": rid, "bytes": size})
        return {"ok": True, "bytes": size,
                "url": f"/markitup/api/reviews/{rid}/proposal"}

    @web_route("POST", "/api/reviews/{rid}/rereview")
    async def api_rereview(self, request) -> dict:
        """Review this review's proposal — the loop's closing move.

        The URL is built here rather than by the page. `_url_refusal` matches
        host+port against this daemon's own config, so a browser reaching us
        through a tailnet name or a reverse proxy would send an origin that
        fails that check even though it is us. Loopback at the configured port
        always passes, and the capture runs on this machine anyway.

        Views are supplied explicitly, which bypasses discovery entirely
        (`source: "explicit"`) — no crawl of our own daemon, and the run costs
        only the review passes.
        """
        rid = request.path_params.get("rid", "")
        err = path_segment_error(rid, "review id")
        if err:
            return {"error": err}
        d = self.review_dir(rid)
        if not d or not (d / PROPOSAL_NAME).exists():
            return {"error": f"no proposal for {rid!r} — upload one first"}
        doc = self.read_review(rid) or {}
        url = own_daemon_url(self.kernel.config, f"markitup/api/reviews/{rid}/proposal")
        base = (doc.get("title") or rid).strip()
        return await self.start_review(
            url=url,
            rubric=str(doc.get("rubric") or "site"),
            title=f"{base} — proposal re-review",
            views=[{"url": url, "title": "Proposal", "selector": "",
                    "focus": doc.get("source") or "The rebuilt page."}],
        )

    @web_route("GET", "/api/run/{rid}/status")
    async def api_run_status(self, request) -> dict:
        rid = request.path_params.get("rid", "")
        err = path_segment_error(rid, "review id")
        if err:
            return {"error": err}
        handle = self._registry().get(rid)
        state = handle.read_state() if handle else None
        if not state:
            return {"error": f"no run with id {rid!r}"}
        return {"run": state}

    # ── comment editing ──────────────────────────────────────────────

    def _find_comment(self, doc: dict, n: int) -> dict | None:
        for c in doc.get("comments") or []:
            if int(c.get("n", -1)) == n:
                return c
        return None

    @web_route("PATCH", "/api/reviews/{rid}")
    async def api_patch_review(self, request) -> dict:
        """Edit the review document itself — today only ``summary``.

        The summary is the review-level argument no pin can carry: the themes,
        the order to tackle them, how the review was made. Markdown, rendered
        on the page and printed first in every export. An empty string clears
        it; a missing key is refused rather than treated as a clear, so a
        client that sends the wrong field cannot silently erase the text.
        """
        rid = request.path_params.get("rid", "")
        err = path_segment_error(rid, "review id")
        if err:
            return {"error": err}
        try:
            body = await request.json()
        except Exception:
            return {"error": "body must be JSON"}
        if not isinstance(body, dict) or "summary" not in body:
            return {"error": "nothing to change — send a summary"}
        text, err = clean_summary(body.get("summary"))
        if err:
            return {"error": err}
        async with self.write_lock(rid):
            doc = self.read_review(rid)
            if not doc:
                return {"error": f"no review with id {rid!r}"}
            doc["summary"] = text
            self._save_review(rid, doc)
        return {"ok": True, "summary": text}

    @web_route("POST", "/api/reviews/{rid}/comments")
    async def api_add_comment(self, request) -> dict:
        """Add a comment by hand. Authored pins are marked ``anchor: human`` so
        they are never mistaken for a measured one."""
        rid = request.path_params.get("rid", "")
        err = path_segment_error(rid, "review id")
        if err:
            return {"error": err}
        body = await request.json()
        async with self.write_lock(rid):
            doc = self.read_review(rid)
            if not doc:
                return {"error": f"no review with id {rid!r}"}
            tag = normalize_tag(doc.get("rubric", "site"), str(body.get("tag") or ""))
            if not tag:
                return {"error": "unknown tag for this review's rubric"}
            shot_id = str(body.get("shot_id") or "")
            if shot_id and shot_id not in {s.get("id") for s in doc.get("shots") or []}:
                return {"error": f"no shot {shot_id!r} in this review"}
            x, y = body.get("x"), body.get("y")
            has_point = x is not None and y is not None
            comment = {
                "n": next_comment_number(doc.get("comments")),
                "tag": tag,
                "el": "",
                "shot_id": shot_id,
                "title": str(body.get("title") or "").strip(),
                "body": str(body.get("body") or "").strip(),
                "x": round(clamp01(x), 5) if has_point else None,
                "y": round(clamp01(y), 5) if has_point else None,
                "anchor": "human" if has_point else "none",
                "status": "open",
                "author": "human",
            }
            if not (comment["title"] or comment["body"]):
                return {"error": "a comment needs a title or a body"}
            doc.setdefault("comments", []).append(comment)
            self._save_review(rid, doc)
        return {"ok": True, "comment": comment}

    @web_route("PATCH", "/api/reviews/{rid}/comments/{n}")
    async def api_patch_comment(self, request) -> dict:
        """Edit one comment: text, tag, status, or pin position."""
        rid = request.path_params.get("rid", "")
        err = path_segment_error(rid, "review id")
        if err:
            return {"error": err}
        try:
            n = int(request.path_params.get("n", ""))
        except (TypeError, ValueError):
            return {"error": "comment number must be an integer"}
        body = await request.json()

        async with self.write_lock(rid):
            doc = self.read_review(rid)
            if not doc:
                return {"error": f"no review with id {rid!r}"}
            c = self._find_comment(doc, n)
            if c is None:
                return {"error": f"no comment {n} in this review"}

            if "tag" in body:
                tag = normalize_tag(doc.get("rubric", "site"), str(body.get("tag") or ""))
                if not tag:
                    return {"error": "unknown tag for this review's rubric"}
                c["tag"] = tag
            if "status" in body:
                status = str(body.get("status") or "").strip().lower()
                if status not in {"open", "accepted", "dismissed"}:
                    return {"error": "status must be open, accepted or dismissed"}
                c["status"] = status
            for field in ("title", "body"):
                if field in body:
                    c[field] = str(body.get(field) or "").strip()
            if "x" in body and "y" in body:
                # A human placement: anchor, element and derived ref all
                # change together (see shared.move_pin).
                move_pin(c, body.get("x"), body.get("y"))
            c["author"] = "both" if c.get("author") == "ai" else c.get("author", "human")
            self._save_review(rid, doc)

        if c.get("status") in {"accepted", "dismissed"}:
            # Off the NORMALISED value, not the raw body: {"status": "Accepted"}
            # persisted the change and fired no event.
            await self.emit("markitup:comment_resolved",
                            {"id": rid, "n": n, "status": c["status"]})
        return {"ok": True, "comment": c}

    @web_route("DELETE", "/api/reviews/{rid}/comments/{n}")
    async def api_delete_comment(self, request) -> dict:
        rid = request.path_params.get("rid", "")
        err = path_segment_error(rid, "review id")
        if err:
            return {"error": err}
        try:
            n = int(request.path_params.get("n", ""))
        except (TypeError, ValueError):
            return {"error": "comment number must be an integer"}
        async with self.write_lock(rid):
            doc = self.read_review(rid)
            if not doc:
                return {"error": f"no review with id {rid!r}"}
            before = len(doc.get("comments") or [])
            doc["comments"] = [c for c in doc.get("comments") or []
                               if int(c.get("n", -1)) != n]
            if len(doc["comments"]) == before:
                return {"error": f"no comment {n} in this review"}
            self._save_review(rid, doc)
        return {"ok": True}

    @web_route("DELETE", "/api/reviews/{rid}")
    async def api_delete(self, request) -> dict:
        """Delete a review and its screenshots."""
        import shutil

        rid = request.path_params.get("rid", "")
        err = path_segment_error(rid, "review id")
        if err:
            return {"error": err}
        d = self.review_dir(rid)
        if not d or not d.exists():
            return {"error": f"no review with id {rid!r}"}
        shutil.rmtree(d, ignore_errors=True)
        return {"ok": True}

    # ── running a review ─────────────────────────────────────────────

    async def start_review(
        self,
        url: str = "",
        rubric: str = "",
        title: str = "",
        views: list | None = None,
        approve: bool = False,
        sources: list | None = None,
    ) -> dict:
        """Start a review — the ``markitup.start_review`` verb.

        With an explicit ``sources`` list (or the legacy ``views``) the run goes
        straight through. With only a ``url`` it pauses after ``discover`` so
        the proposed shot list can be approved before any capture or token
        spend — pass ``approve=True`` to skip that gate deliberately.

        A source is ``{kind, ref, title?}`` — ``kind`` one of web, document,
        pdf, image; ``ref`` a URL or, for a document, ``<root>/<path>`` under an
        allow-listed root (sources.py). Every source is checked here so a bad
        one is an in-band error before a run exists.
        """
        # Strip any credential the caller pasted in BEFORE it is persisted. The
        # daemon mints deep-links of exactly this shape ("/hub/?token=..."), so
        # pasting one is the expected mistake — and `inputs` is written verbatim
        # to run.json, copied to review.json's `source`, rendered on the hub
        # panel, and printed as `Route:` in the exported PDF that lands in the
        # vault. Nothing is lost: authed_url re-mints a token from config when
        # the host is this daemon, so sign-in still works.
        url = strip_token((url or "").strip())
        # Loud before lossy: normalize_sources DROPS a malformed row, which is
        # right for a stored run and wrong at the request boundary — a list of
        # nothing but unknown kinds must not quietly become a bare-URL discovery.
        bad = invalid_source_rows(sources)
        if bad:
            return {"error": bad}
        explicit = normalize_sources({"sources": sources, "views": views})
        if not url and not explicit:
            return {"error": "give a url to review, or an explicit list of sources"}
        if url:
            refusal = await self._url_refusal(url)
            if refusal:
                return {"error": refusal}
        for src in explicit:
            if src["kind"] == "web":
                src["ref"] = strip_token(src["ref"])
                refusal = await self._url_refusal(src["ref"])
            else:
                refusal = self.source_refusal(src)
            if refusal:
                return {"error": refusal}

        rubric = (rubric or self.setting_or_config("markitup.rubric", "site")).strip()
        if rubric not in RUBRICS:
            return {"error": f"unknown rubric {rubric!r} — one of {rubric_ids()}"}

        pipe = self.build_pipeline()
        inputs: dict[str, Any] = {
            "url": url,
            "rubric": rubric,
            "title": title,
            "sources": explicit,
            "_now": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        stop = None if (explicit or approve) else "discover"
        await self.emit("markitup:review_started", {"url": url, "rubric": rubric})
        return await pipe.start(inputs, stop_after=stop, budget=self.make_budget())

    @web_route("POST", "/api/run")
    async def api_start(self, request) -> dict:
        body = await request.json()
        return await self.start_review(
            url=str(body.get("url") or ""),
            rubric=str(body.get("rubric") or ""),
            title=str(body.get("title") or ""),
            views=body.get("views") or None,
            approve=bool(body.get("approve")),
            sources=body.get("sources") or None,
        )

    @web_route("POST", "/api/run/{rid}/resume")
    async def api_resume(self, request) -> dict:
        """Continue a paused run — after approving a shot list, or after a
        failure that has been dealt with."""
        rid = request.path_params.get("rid", "")
        err = path_segment_error(rid, "review id")
        if err:
            return {"error": err}
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        if rid not in self._registry():
            return {"error": f"no run with id {rid!r}"}
        pipe = self.build_pipeline()
        extra = {"views": body["views"]} if body.get("views") else None
        return await pipe.resume(rid, inputs=extra, budget=self.make_budget())

    # ── export ───────────────────────────────────────────────────────

    @web_route("POST", "/api/reviews/{rid}/export")
    async def api_export(self, request) -> dict:
        rid = request.path_params.get("rid", "")
        err = path_segment_error(rid, "review id")
        if err:
            return {"error": err}
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        fmt = str(body.get("format") or "markdown").lower()
        if fmt == "pdf":
            return await self.export_pdf(rid)
        if fmt == "markdown":
            return await self.export_markdown(rid)
        return {"error": "format must be 'markdown' or 'pdf'"}

    # ── hub ──────────────────────────────────────────────────────────

    async def panel_runs(self) -> list[dict] | None:
        """Runs that stopped and need a person — the approval gate is the
        normal case here, not an error state."""
        return self.stopped_runs_panel(
            # A bare list link, deliberately: stopped_run_rows uses this href
            # verbatim and never sees the run id (it iterates states, not
            # handles), so "#{run_id}" would render literally. The list is a
            # real destination now that list_reviews includes paused runs.
            href="/markitup/",
            kind="reviews",
            label_fields=("title", "url"),
            total_stages=len(_stages.STAGES),
        )
