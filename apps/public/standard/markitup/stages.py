"""markitup — the pipeline stage table.

Extracted from app.py to keep the spine atomic (CLAUDE.md rule 4). Owns: the
ordered stages of one review run and the per-run budget that bounds it.

A review is a ``Pipeline`` rather than a loop because it meets all three tests
in `.claude/rules/staged-pipeline.md`: several ordered stages, more than one of
them genuinely expensive (browser time, then tokens, then cloud vision), and a
real preview seam. Resume is not a nicety here — a run that dies in `review`
would otherwise re-pay for every capture.

``discover`` is deliberately first and cheap, and it is where a bare-URL run
pauses. Approving a shot list costs one model call; discovering it *after*
capturing eight views would cost the captures too.

Stages are module-level functions taking ``ctx``, never bound methods — they
reach the app through ``ctx.app``.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: none at import time (the app's own bound methods
are called through ctx.app).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from emptyos.sdk.pipeline import Pipeline, Stage
from emptyos.sdk.run_budget import BudgetApprovalRequired, BudgetExceeded, RunBudget

from .shared import next_comment_number, normalize_sources

if TYPE_CHECKING:
    from .app import MarkitupApp  # noqa: F401 — for type hints only


# ─── Bind to MarkitupApp class as ────────────────────────────────────
#   build_pipeline = _stages.build_pipeline
#   make_budget    = _stages.make_budget
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────

# A single capture's default width, and the default *set* a run sweeps. These
# must match `markitup.viewports` in manifest.toml: setting_or_config falls back
# to the literal below, never to the manifest's declared default, so a mismatch
# means the settings panel documents a default the code does not use. That
# shipped once — the manifest promised desktop+mobile and every run captured
# desktop only. tests/test_unit_markitup.py pins the two together.
DEFAULT_VIEWPORT = "1440x900"
DEFAULT_VIEWPORTS = "1440x900,390x844"


def _viewports(app) -> list[str]:
    """Configured capture widths, always at least one.

    An empty or malformed setting falls back rather than capturing nothing: a
    run that silently produced zero shots would look like a model failure.
    """
    raw = str(app.setting_or_config("markitup.viewports", DEFAULT_VIEWPORTS) or "")
    out = [v.strip() for v in raw.split(",") if v.strip()]
    return out or [DEFAULT_VIEWPORT]


def _tokens_for(*texts: str) -> int:
    """Rough prompt-token estimate for the budget.

    ``RunBudget.spend`` forwards **params to ``estimate_cost``; with none, the
    cost table falls through to 0.0, so every call was free and the cap could
    never fire — and ``snapshot()`` recorded ``spent_usd: 0.0`` into review.json
    as a *false audit record*. A crude 4-chars-per-token estimate is wrong in
    the third digit and right about whether $2 has been spent.
    """
    return max(1, sum(len(t or "") for t in texts) // 4)


def _ctx_id(ctx) -> str:
    """One browser context per run, so cookies and viewport state cannot leak
    between two reviews running back to back."""
    return f"markitup-{ctx.handle.run_id}"


# ── discover ─────────────────────────────────────────────────────────

async def _discover(ctx):
    """Decide which sources to review. Proposes only — captures nothing.

    An explicit ``sources`` (or legacy ``views``) list is taken as given; only a
    bare web ``url`` is discovered from. A document source has nothing to
    discover — it is one file — so it never reaches the model here.
    """
    explicit = normalize_sources({k: ctx.inputs.get(k) for k in ("sources", "views")})
    if explicit:
        await ctx.progress(1.0, f"{len(explicit)} source(s) supplied")
        return {"sources": explicit, "views": _as_views(explicit),
                "source": "explicit", "considered": 0}

    url = (ctx.inputs.get("url") or "").strip()
    if not url:
        raise ValueError("a review needs either a url to discover from, or an explicit source list")

    app = ctx.app
    async with ctx.budget.spend("think", label="discover",
                                tokens=_tokens_for(url) + 2_000):
        found = await app.propose_views(
            url=url,
            context_id=_ctx_id(ctx),
            max_views=int(app.setting_or_config("markitup.max_views", 8) or 8),
            crawl_budget=int(app.setting_or_config("markitup.crawl_budget", 40) or 40),
        )
    await ctx.progress(1.0, f"{len(found['views'])} view(s) from {found['considered']} link(s)")
    return {"views": found["views"], "sources": normalize_sources({"views": found["views"]}),
            "source": "discovered", "considered": found["considered"]}


def _as_views(sources: list[dict]) -> list[dict]:
    """The legacy ``views`` shape of a source list, for the approval gate UI,
    which ticks rows by ``url``. A document source's ``url`` is its ref."""
    return [{"url": s["ref"], "title": s.get("title", ""), "kind": s["kind"],
             "selector": s.get("selector", ""), "focus": s.get("focus", "")}
            for s in sources]


# ── capture ──────────────────────────────────────────────────────────

async def _capture(ctx):
    """Produce shots for every source, whatever its kind, measuring as we go.

    The stage knows nothing about kinds: ``app.produce_shots`` dispatches to the
    producer (sources.py). A web source is captured once per configured
    viewport; other producers decide their own shot count.
    """
    app = ctx.app
    # An approved list supplied on resume WINS over the discover result. Reading
    # only the stage result made the approval gate decorative: `discover` is
    # already complete, so resume skips it, and un-ticking a view captured it
    # anyway — the exact spend the gate exists to prevent. Verified live before
    # the fix: 2 views approved, 5 captured.
    sources = normalize_sources({k: ctx.inputs.get(k) for k in ("sources", "views")})
    origin = "approved"
    if not sources:
        disc = ctx.result("discover") or {}
        sources = disc.get("sources") or normalize_sources({"views": disc.get("views") or []})
        origin = "discovered"
    if not sources:
        raise ValueError("nothing to capture — discovery returned no sources")

    viewports = _viewports(app)
    shots_dir = ctx.handle.dir / "shots"
    cid = _ctx_id(ctx)
    total = len(sources)
    shots, failures = [], []
    done = 0
    try:
        for src in sources:
            label = f"{src.get('title') or src.get('ref')} [{src.get('kind')}]"
            try:
                shots.extend(await app.produce_shots(
                    src, viewports=viewports, shots_dir=shots_dir, context_id=cid))
            except Exception as e:  # noqa: BLE001 — one bad source must not void the run
                failures.append({"view": label, "error": str(e) or type(e).__name__})
            done += 1
            await ctx.progress(done / max(1, total), label)
    finally:
        try:
            await app.browse("close", context_id=cid)
        except Exception:
            pass

    if not shots:
        raise ValueError(
            "every source failed to produce a shot: "
            + "; ".join(f["error"] for f in failures[:3])
        )
    # Page text is per-shot and large; it is only needed by the review stage,
    # which reads it from this result. It never reaches review.json.
    return {"shots": shots, "failures": failures, "views_from": origin,
            "sources": sources}


# ── review ───────────────────────────────────────────────────────────

async def _review(ctx):
    """Draft comments for every captured shot."""
    app = ctx.app
    shots = (ctx.result("capture") or {}).get("shots") or []
    rubric_id = ctx.inputs.get("rubric") or app.setting_or_config("markitup.rubric", "site")
    cap = int(app.setting_or_config("markitup.max_comments", 12) or 12)

    # Resolved once, outside the loop — see review_system's docstring on
    # prefix-cache stability.
    system = app.review_system(rubric_id)

    comments, failures, diagnostics = [], [], []
    for i, shot in enumerate(shots, 1):
        sidecar = ctx.handle.dir / "shots" / f"{shot['id']}.anchors.json"
        try:
            side = json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:
            side = {"anchors": [], "origin": shot.get("origin")}
        est = _tokens_for(shot.get("text", ""), system) + 40 * len(side.get("anchors") or [])
        try:
            async with ctx.budget.spend("think", label=f"review:{shot['id']}",
                                        tokens=est):
                got = await app.review_shot(
                    shot=shot,
                    anchors=side.get("anchors") or [],
                    origin=side.get("origin") or shot.get("origin"),
                    rubric_id=rubric_id,
                    system=system,
                    cap=cap,
                    start_n=next_comment_number(comments),
                )
            comments.extend(got["comments"])
            diagnostics.append(got["stats"])
        except (BudgetExceeded, BudgetApprovalRequired):
            # Must NOT be swallowed. These are how the pipeline pauses a run for
            # approval or stops it at the cap; catching them here recorded a
            # "failure" and then spent again on the next shot, which is the
            # opposite of a ceiling (.claude/rules/audits.md on a defensive
            # except inside a gate).
            raise
        except Exception as e:  # noqa: BLE001 — a failed shot loses its comments, not the run
            failures.append({"shot": shot.get("id"), "error": str(e) or type(e).__name__})
        await ctx.progress(i / max(1, len(shots)), shot.get("title", ""))

    return {"comments": comments, "rubric": rubric_id,
            "failures": failures, "diagnostics": diagnostics}


# ── place ────────────────────────────────────────────────────────────

async def _place(ctx):
    """L3: pin the comments the DOM pass could not place.

    Dark by default. When off this is a genuine no-op, so the default path
    spends nothing on vision and every pin in a review is a measured one.
    """
    app = ctx.app
    comments = (ctx.result("review") or {}).get("comments") or []
    unplaced = [c for c in comments if c.get("anchor") == "none"]
    if not unplaced or not app.app_config("feature.vision-pins.enabled", False):
        return {"placed": 0, "unplaced": len(unplaced), "enabled": False}
    placed = await app.place_by_vision(ctx=ctx, comments=comments)
    return {"placed": placed, "unplaced": len(unplaced) - placed, "enabled": True}


# ── assemble ─────────────────────────────────────────────────────────

async def _assemble(ctx):
    """Write review.json — the document every surface reads."""
    app = ctx.app
    cap_res = ctx.result("capture") or {}
    rev_res = ctx.result("review") or {}
    disc = ctx.result("discover") or {}

    shots = []
    for s in cap_res.get("shots") or []:
        s = dict(s)
        s.pop("text", None)  # prompt input, not review content
        shots.append(s)

    diag = rev_res.get("diagnostics") or []
    comments = rev_res.get("comments") or []
    # Anchoring health is part of the document, not a log line. A review where
    # nothing could be anchored is still useful prose, but it is NOT the product
    # this app promises, and the surface has to be able to say so instead of
    # quietly rendering a picture with no pins on it.
    anchoring = {
        "comments": len(comments),
        "anchored": sum(1 for c in comments if c.get("anchor") == "dom"),
        "named_an_element": sum(int(d.get("named_an_element") or 0) for d in diag),
        "unresolved_ids": sorted({e for d in diag for e in (d.get("unresolved_ids") or [])})[:6],
    }

    sources = cap_res.get("sources") or []
    doc = {
        "id": ctx.handle.run_id,
        "title": (ctx.inputs.get("title") or "").strip()
        or (shots[0]["title"] if shots else ctx.inputs.get("url", "Review")),
        # `source` keeps its historical meaning (the URL a run started from);
        # for a run that started from a source list it is the first ref, so the
        # list view and the export header still have one line to print.
        "source": ctx.inputs.get("url", "") or (sources[0]["ref"] if sources else ""),
        "sources": sources,
        "rubric": rev_res.get("rubric") or "site",
        "created": ctx.inputs.get("_now", ""),
        "shots": shots,
        "comments": comments,
        "anchoring": anchoring,
        "discovery": {k: disc.get(k) for k in ("source", "considered")},
        "failures": (cap_res.get("failures") or []) + (rev_res.get("failures") or []),
        "budget": ctx.budget.snapshot() if ctx.budget else None,
    }
    ctx.handle.write_state(doc, name="review.json")
    await app.emit("markitup:review_ready", {
        "id": doc["id"], "shots": len(shots), "comments": len(doc["comments"]),
    })
    return {"shots": len(shots), "comments": len(doc["comments"])}


STAGES = [
    Stage("discover", _discover, weight=1),
    Stage("capture", _capture, weight=5),
    Stage("review", _review, weight=5),
    Stage("place", _place, weight=1),
    Stage("assemble", _assemble, weight=1),
]


def build_pipeline(self) -> Pipeline:
    """The review pipeline, backed by the same registry the UI lists from."""
    return Pipeline(app=self, name="markitup", stages=STAGES, registry_kind="reviews")


def make_budget(self) -> RunBudget:
    """Per-run ceiling from settings. 0 (or less) means observe-only.

    ``cap`` mode is the point: a one-click run on an unfamiliar site should stop
    and ask rather than discover forty views and review them all.
    """
    total = float(self.setting_or_config("markitup.run_budget_usd", 2.0) or 0)
    if total <= 0:
        return RunBudget(mode="observe")
    return RunBudget(total_usd=total, mode="cap", per_action_usd=max(0.25, total / 4))
