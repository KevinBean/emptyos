"""Panel aggregation — turn `[[contributes.*.panel]]` entries into rendered rows.

The shared half of a *panel host*: given a list of contributions, call each
contributor and return `{id, title, renderer, group, priority, source, data,
lazy}` dicts in layout order. What stays with the host is the part that
genuinely differs — **which slots it gathers**. Hub reads `("hub", "panel")`;
the life dashboard reads the union of `("hub-life", "panel")` and
`("hub", "panel")`, which is the whole reason that parallel namespace exists
(`.claude/rules/hub-panels.md` — reviewed 2026-07-10 and deliberately kept).

Extracted 2026-08-16 at the second host, per CLAUDE.md rule 9. The two copies
were byte-identical in behaviour and had already drifted once in a way that
mattered: the single-panel `only=` narrowing was fixed in hub and not in
hub-life, so the life dashboard kept re-running every contributor — including
a 60s cached LLM synthesis — on each lazy hydration.

No kernel, no `self`, no I/O of its own: the host injects `call` and `on_error`.
That is what lets the aggregation logic be tested without a daemon.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable

from emptyos.sdk.utils import contribution_id


def panel_debug_page() -> Path:
    """The shared `/‹board›/debug/panels` page, for any host that has panels.

    It lives in the platform static dir beside the other route-served pages
    (`topology.html`, `system.html`, `console.html`) because it introspects a
    *platform* contract — panel contributions — not one app's data. It was a
    byte-identical copy in two apps' `pages/` for exactly one day, which is one
    day of nothing keeping them in sync. The page derives its API base from the
    URL it was served at, so a single file serves every board.
    """
    return Path(__file__).resolve().parents[1] / "web" / "static" / "panel-debug.html"


def _row(contrib: dict, *, data: Any, lazy: bool, unavailable: str = "") -> dict:
    """One panel row. Single constructor so the placeholder and the called
    form cannot disagree about shape — they differ only in `data`/`lazy`.

    ``unavailable`` is a human reason the row has no data even though the
    contributor was asked (today: the budget expired). It is empty on every
    healthy row, so a consumer can treat truthiness as "this one didn't
    hydrate" without knowing why.
    """
    return {
        "id": contribution_id(contrib),
        "title": contrib.get("title") or "",
        "renderer": contrib.get("renderer") or "plain-list",
        "group": contrib.get("group") or "",
        "priority": int(contrib.get("priority", 100) or 100),
        "source": contrib.get("_app_id"),
        "data": data,
        "lazy": lazy,
        "unavailable": unavailable,
    }


# Default per-contributor budget for a full-board hydrate. 12 s sits under the
# test client's 15 s default (tests/conftest.py) so a board that hits the budget
# still answers inside it, and above the slowest healthy lazy panel measured on
# 2026-09-12 (two LLM-backed panels at 9.5 s each, everything else < 0.3 s).
DEFAULT_PANEL_BUDGET_S = 12.0


def parse_panel_budget(raw: Any, default: float = DEFAULT_PANEL_BUDGET_S) -> float | None:
    """Turn a `panel_timeout_s` config value into a `resolve_panels` budget.

    ``0`` or a negative number means **no budget** (``None``) — the usual
    "0 disables the limit" convention, and the only way to select unbounded
    from TOML, which cannot express null. An unparseable value falls back to
    ``default`` rather than silently disabling the limit. Shared by every
    panel host so the two boards cannot drift on the edge cases.
    """
    if raw is None:
        return default
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return default
    if val != val or val <= 0:  # NaN or non-positive → unbounded
        return None
    return val


async def resolve_panels(
    contributions: list[dict],
    *,
    call: Callable[[str, str], Awaitable[Any]],
    include_lazy: bool = False,
    only: str = "",
    on_error: Callable[[str], None] | None = None,
    timeout_s: float | None = None,
) -> list[dict]:
    """Call each contributor and return its row, sorted by (priority, id).

    ``timeout_s`` is a per-contributor budget. Without it one stuck contributor
    holds the whole board — ``gather`` waits for the slowest — which is how
    ``/hub/api/panels/all`` swung between 12 s and >90 s on the same tree
    (2026-09-12) and timed out every test that read it. ``None`` keeps the
    unbounded behaviour for callers that want a single slow panel to finish
    (a 60 s cached LLM synthesis hydrated on its own).

    A panel past the budget is **marked, not removed**: its row comes back with
    ``data=None`` and a human ``unavailable`` reason. That matters because the
    budget cannot distinguish a stuck panel from a healthy one queued behind a
    shared lock, and the only caller that sets one is a debug listing whose
    product is the complete set of contributions. A contributor that *raises*
    is still dropped — that is the pre-existing fail-soft contract.

    ``only`` narrows to one panel id **before anything is called**. This is
    load-bearing, not an optimisation: without it a single-panel refresh ran
    every contributor and discarded all but one, so it cost the whole board
    (~4.1s idle on a 114-panel hub, far worse under boot load) and defeated
    every `lazy` declaration it was supposed to honour. The symptom was
    unreadable — two unrelated panels timed identically, because neither figure
    was ever about the panel being asked for.

    Fail-soft per panel: a contributor that raises, or returns ``None`` ("nothing
    to show right now"), drops out of the list rather than breaking the board.

    ``lazy`` contributions are emitted as data-less placeholders unless
    ``include_lazy``. A host's single-panel endpoint passes ``include_lazy=True``
    *with* ``only`` — that pairing is what makes it the lazy-load trigger for one
    panel instead of for all of them.
    """
    if only:
        contributions = [c for c in contributions if contribution_id(c) == only]
    if not contributions:
        return []

    async def _call_one(contrib: dict) -> dict | None:
        app_id = contrib.get("_app_id")
        method = contrib.get("method")
        if not app_id or not method:
            return None
        budget_cm = None
        try:
            if timeout_s is not None:
                # asyncio.timeout (not wait_for) so a TimeoutError the
                # contributor raised ITSELF — a socket timeout, an inner
                # wait_for — is not mistaken for the budget expiring:
                # `expired()` is true only when this budget fired. On 3.11+
                # asyncio.TimeoutError is the builtin TimeoutError, so the two
                # are otherwise indistinguishable.
                async with asyncio.timeout(timeout_s) as budget_cm:
                    data = await call(app_id, method)
            else:
                data = await call(app_id, method)
        except TimeoutError as e:
            expired = budget_cm is not None and budget_cm.expired()
            msg = f"timed out after {timeout_s:g}s" if expired else f"failed: {e!r}"
            if on_error:
                on_error(f"panel '{contrib.get('id')}' ({app_id}.{method}) {msg}")
            # A budget expiry MARKS the row, it never deletes it. The only
            # caller that sets a budget is a debug listing, whose product IS
            # the complete set of contributions — and the budget cannot tell a
            # stuck panel from a healthy one queued behind a shared lock.
            # Measured 2026-09-12: the think chain's first provider holds a
            # one-slot semaphore, so a second LLM-backed panel that answers in
            # 0.0s alone waits out a 12s budget during a full-board hydrate.
            # Dropping it silently deleted a working panel from the one page
            # that exists to enumerate them. The single-panel refresh is
            # unbudgeted, so a marked row is one ↻ away from its real data.
            if expired:
                return _row(contrib, data=None, lazy=False, unavailable=msg)
            return None
        except Exception as e:  # noqa: BLE001 — fail-soft is the contract
            if on_error:
                on_error(
                    f"panel '{contrib.get('id')}' ({app_id}.{method}) failed: {e}"
                )
            return None
        if data is None:
            return None
        cap = contrib.get("limit")
        if isinstance(data, list) and isinstance(cap, int) and cap > 0:
            data = data[:cap]
        return _row(contrib, data=data, lazy=False)

    placeholders: list[dict] = []
    eager_contribs: list[dict] = []
    for c in contributions:
        if c.get("lazy") and not include_lazy:
            placeholders.append(_row(c, data=None, lazy=True))
        else:
            eager_contribs.append(c)

    results = await asyncio.gather(*[_call_one(c) for c in eager_contribs])
    panels = [p for p in results if p is not None] + placeholders
    panels.sort(key=lambda p: (p["priority"], p["id"]))
    return panels
