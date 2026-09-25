"""markitup — L3: placing the comments the DOM pass could not.

Extracted from app.py to keep the spine atomic (CLAUDE.md rule 4). Owns: the
vision fallback that asks a model to locate a comment's subject on the
screenshot itself.

**This is the fallback, never the default.** A pin from the DOM pass is measured
and exact; a pin from here is a model's estimate. Every comment it places is
stamped ``anchor: "vision"`` so the surface can say which is which — presenting
an estimate with the same confidence as a measurement is the specific dishonesty
this app was built to avoid.

Dark by default (``feature.vision-pins.enabled``). It is also the only path that
sends a client's screenshots to a cloud model, so it is gated twice: the flag,
and the standard cloud-consent gate inside the capability.

A convenient coincidence makes the arithmetic trivial: flipbook's refine pass
returns percentages *of the image*, and a shot's image is exactly its captured
region — the whole page for a full-page shot, the crop for a selector one. So
``pct / 100`` is already the normalised coordinate, with no origin translation.

Cross-module callers reach methods here via ``self.X`` after re-binding.
Reaches into other modules: none (flipbook + shared are SDK/leaf).
Do not import from ``.app`` (it imports us, which would cycle).
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING

from emptyos.sdk.flipbook import build_refine_messages, parse_refine_anchors
from emptyos.sdk.run_budget import BudgetApprovalRequired, BudgetExceeded
from emptyos.sdk.utils import parse_llm_json

from .shared import clamp01

if TYPE_CHECKING:
    from .app import MarkitupApp  # noqa: F401 — for type hints only


# ─── Bind to MarkitupApp class as ────────────────────────────────────
#   place_by_vision = _vision.place_by_vision
# Adding a new method here? Add a matching binding line in app.py.
# ────────────────────────────────────────────────────────────────────

# Vision calls are per-image and cloud-billed; a shot with more unplaced
# comments than this is a sign the review went wide, not that we should pay to
# locate all of them.
MAX_PER_SHOT = 8


async def place_by_vision(self, *, ctx, comments: list[dict]) -> int:
    """Locate unplaced comments on their shot. Returns how many were placed.

    Mutates ``comments`` in place. Never raises: a failed vision call leaves the
    comments unplaced, which is the same state they were already in — the run
    must not die because an optional enrichment did.
    """
    by_shot: dict[str, list[dict]] = {}
    for c in comments:
        if c.get("anchor") == "none" and c.get("shot_id"):
            by_shot.setdefault(c["shot_id"], []).append(c)
    if not by_shot:
        return 0

    shots = {s["id"]: s for s in (ctx.result("capture") or {}).get("shots") or []}
    placed = 0

    for shot_id, group in by_shot.items():
        shot = shots.get(shot_id)
        if not shot:
            continue
        png = ctx.handle.dir / "shots" / shot.get("image", "")
        if not png.exists():
            continue
        group = group[:MAX_PER_SHOT]
        try:
            b64 = base64.b64encode(png.read_bytes()).decode("ascii")
            callouts = [{"label": c.get("title") or c.get("body", "")[:60]} for c in group]
            async with ctx.budget.spend("think", label=f"vision:{shot_id}",
                                        tokens=len(b64) // 3):
                raw = await self.think(
                    messages=build_refine_messages(callouts, b64),
                    domain="text",
                    temperature=0.1,
                )
            data = parse_llm_json(raw if isinstance(raw, str) else str(raw), fallback={})
            anchors = parse_refine_anchors(
                (data or {}).get("anchors") if isinstance(data, dict) else data,
                len(callouts),
            )
        except (BudgetExceeded, BudgetApprovalRequired):
            raise  # the cap must stop the run, not be absorbed as "no pins"
        except Exception:  # noqa: BLE001 — optional enrichment, never fatal
            continue

        seen_idx = set()
        for a in anchors:
            if a["idx"] in seen_idx:
                continue  # a repeated idx would double-count -> unplaced: -1
            seen_idx.add(a["idx"])
            c = group[a["idx"]]
            # flipbook returns 0-100 percentages of the image, and the image is
            # the captured region — so this is already the normalised value.
            c["x"] = round(clamp01(a["x"] / 100.0), 5)
            c["y"] = round(clamp01(a["y"] / 100.0), 5)
            c["anchor"] = "vision"
            placed += 1

    return placed
