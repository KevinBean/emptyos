"""Offline unit tests for ``emptyos.sdk.panels.resolve_panels``.

The aggregator extracted from hub + hub-life at the second host. It takes an
injected ``call``, so the whole contract is testable with no daemon and no
kernel.

The load-bearing case is ``only=``: it must narrow *before* anything is called.
A version that filters the returned rows produces the same output for every
assertion about ids — which is exactly how the bug survived in two hubs — so
these tests record **which contributors ran**.
"""

from __future__ import annotations

import asyncio

from emptyos.sdk.panels import (
    DEFAULT_PANEL_BUDGET_S,
    panel_debug_page,
    parse_panel_budget,
    resolve_panels,
)


def _contribs():
    return [
        {"_app_id": "task", "method": "panel_a", "id": "task-a", "priority": 50},
        {"_app_id": "rooms", "method": "panel_b", "id": "rooms-b", "priority": 10},
        {"_app_id": "kb", "method": "panel_c", "id": "kb-c", "lazy": True},
        {"_app_id": "repo", "method": "panel_d"},  # no id → derived
    ]


def _run(contribs, *, returns=None, raises=(), **kw):
    """Returns (panels, called). ``returns`` maps 'app.method' → data."""
    called: list[str] = []
    errors: list[str] = []

    async def call(app_id, method):
        key = f"{app_id}.{method}"
        called.append(key)
        if key in raises:
            raise RuntimeError("boom")
        return (returns or {}).get(key, [{"title": key}])

    panels = asyncio.run(
        resolve_panels(contribs, call=call, on_error=errors.append, **kw)
    )
    return panels, called, errors


class TestTimeoutBudget:
    """One stuck contributor must not hold the board (`gather` waits for the
    slowest). The budget drops and reports it; without a budget the old
    unbounded behaviour is preserved byte-for-byte."""

    def _run_timed(self, *, timeout_s, slow_s=0.3):
        called: list[str] = []
        errors: list[str] = []

        async def call(app_id, method):
            called.append(f"{app_id}.{method}")
            if app_id == "slow":
                await asyncio.sleep(slow_s)
            return [{"title": app_id}]

        contribs = [
            {"_app_id": "fast", "method": "panel_a", "id": "fast-a"},
            {"_app_id": "slow", "method": "panel_b", "id": "slow-b"},
        ]
        panels = asyncio.run(
            resolve_panels(contribs, call=call, on_error=errors.append, timeout_s=timeout_s)
        )
        return [p["id"] for p in panels], errors

    def test_over_budget_contributor_is_marked_not_deleted(self):
        # A budget expiry must never remove a contribution from the listing:
        # the budget cannot tell a stuck panel from a healthy one queued behind
        # a shared lock, and the only caller that sets one is the debug view
        # whose product IS the complete set. Measured 2026-09-12: two healthy
        # LLM panels vanished from /hub/api/panels/all this way.
        ids, errors = self._run_timed(timeout_s=0.05)
        assert ids == ["fast-a", "slow-b"], ids
        assert len(errors) == 1 and "slow-b" in errors[0] and "timed out" in errors[0], errors

    def test_the_marked_row_carries_no_data_and_a_reason(self):
        called: list[str] = []

        async def call(app_id, method):
            called.append(app_id)
            if app_id == "slow":
                await asyncio.sleep(0.3)
            return [{"title": app_id}]

        contribs = [
            {"_app_id": "fast", "method": "panel_a", "id": "fast-a"},
            {"_app_id": "slow", "method": "panel_b", "id": "slow-b"},
        ]
        panels = asyncio.run(resolve_panels(contribs, call=call, timeout_s=0.05))
        marked = [p for p in panels if p["id"] == "slow-b"][0]
        healthy = [p for p in panels if p["id"] == "fast-a"][0]
        assert marked["data"] is None
        assert "timed out after" in marked["unavailable"]
        # A healthy row must stay falsy on the same field, so a consumer can
        # branch on truthiness without knowing the reason vocabulary.
        assert not healthy["unavailable"]
        assert healthy["data"] == [{"title": "fast"}]

    def test_a_raising_contributor_is_still_dropped(self):
        # Only the BUDGET marks; the pre-existing fail-soft contract for a
        # contributor that raises is unchanged.
        panels, _called, errors = _run(_contribs(), raises=("task.panel_a",), timeout_s=5.0)
        assert "task-a" not in [p["id"] for p in panels]
        assert len(errors) == 1

    def test_under_budget_contributor_survives(self):
        ids, errors = self._run_timed(timeout_s=2.0, slow_s=0.05)
        assert ids == ["fast-a", "slow-b"], ids
        assert errors == []

    def test_no_budget_waits_for_the_slow_one(self):
        ids, errors = self._run_timed(timeout_s=None, slow_s=0.1)
        assert ids == ["fast-a", "slow-b"], ids
        assert errors == []

    def _run_raising_timeout(self, *, timeout_s):
        """A contributor that raises TimeoutError ITSELF (a socket timeout, an
        inner wait_for) — on 3.11+ the same class the budget uses."""
        errors: list[str] = []

        async def call(app_id, method):
            if app_id == "sock":
                raise TimeoutError("socket timed out")
            return [{"title": app_id}]

        contribs = [
            {"_app_id": "ok", "method": "panel_a", "id": "ok-a"},
            {"_app_id": "sock", "method": "panel_b", "id": "sock-b"},
        ]
        panels = asyncio.run(
            resolve_panels(contribs, call=call, on_error=errors.append, timeout_s=timeout_s)
        )
        return [p["id"] for p in panels], errors

    def test_contributor_raised_timeout_is_fail_soft_without_a_budget(self):
        # Regression: the first budget branch formatted `timeout_s` (None here)
        # inside the handler and raised TypeError out of gather — a 500 on the
        # home board for a panel that merely lost a socket.
        ids, errors = self._run_raising_timeout(timeout_s=None)
        assert ids == ["ok-a"], ids
        assert len(errors) == 1 and "sock-b" in errors[0] and "failed" in errors[0], errors
        assert "timed out after" not in errors[0], errors

    def test_contributor_raised_timeout_is_not_blamed_on_the_budget(self):
        ids, errors = self._run_raising_timeout(timeout_s=5.0)
        assert ids == ["ok-a"], ids
        assert len(errors) == 1 and "failed" in errors[0] and "timed out after" not in errors[0], errors


class TestSharedDebugPage:
    """One page serves every panel host. It was a byte-identical copy in two
    apps' `pages/` for a day; the helper's `parents[1]` path math is now the
    single thing that can break, and it breaks silently (a 404 on a debug
    route nobody visits daily), so it gets a pin."""

    def test_helper_resolves_to_the_real_file(self):
        p = panel_debug_page()
        assert p.exists(), f"panel debug page not found at {p}"
        assert p.name == "panel-debug.html"

    def test_the_page_is_the_board_agnostic_one(self):
        # The shared page derives its API base from the URL it was served at.
        # A copy hardwired to one board is what this replaced.
        text = panel_debug_page().read_text(encoding="utf-8")
        assert "location.pathname" in text
        assert "fetch('/hub/api/" not in text, "page is hardwired to the core hub"


class TestParsePanelBudget:
    """The config knob both hub hosts read. `0` must mean unbounded — a
    literal float(0) budget drops every contributor instantly and renders an
    empty board that looks like 'no panels registered'."""

    def test_zero_and_negative_disable_the_budget(self):
        assert parse_panel_budget(0) is None
        assert parse_panel_budget("0") is None
        assert parse_panel_budget(-5) is None

    def test_positive_values_parse(self):
        assert parse_panel_budget(5) == 5.0
        assert parse_panel_budget("2.5") == 2.5

    def test_missing_or_garbage_falls_back_to_default(self):
        assert parse_panel_budget(None) == DEFAULT_PANEL_BUDGET_S
        assert parse_panel_budget("abc") == DEFAULT_PANEL_BUDGET_S
        assert parse_panel_budget("abc", default=7.0) == 7.0
        assert parse_panel_budget(float("nan")) is None

    def test_default_sits_under_the_test_client_timeout(self):
        # tests/conftest.py's shared httpx default is 15 s; a board that hits
        # the budget must still answer inside it.
        assert 0 < DEFAULT_PANEL_BUDGET_S < 15.0


class TestOnlyNarrowsBeforeCalling:
    def test_only_runs_the_one_contributor(self):
        panels, called, _ = _run(_contribs(), include_lazy=True, only="rooms-b")
        assert [p["id"] for p in panels] == ["rooms-b"]
        assert called == ["rooms.panel_b"], (
            f"expected one call, got {called} — narrowing happened after the "
            "calls, which is the whole defect"
        )

    def test_lazy_can_be_forced_alone(self):
        panels, called, _ = _run(_contribs(), include_lazy=True, only="kb-c")
        assert [p["id"] for p in panels] == ["kb-c"]
        assert called == ["kb.panel_c"]

    def test_derived_id_is_addressable(self):
        """A contribution with no explicit id is reachable by its derived one."""
        panels, called, _ = _run(_contribs(), include_lazy=True, only="repo:panel_d")
        assert [p["id"] for p in panels] == ["repo:panel_d"]
        assert called == ["repo.panel_d"]

    def test_unknown_id_costs_nothing(self):
        panels, called, _ = _run(_contribs(), include_lazy=True, only="nope")
        assert panels == []
        assert called == []


class TestLazyContract:
    def test_lazy_is_a_placeholder_by_default(self):
        panels, called, _ = _run(_contribs())
        lazy = [p for p in panels if p["id"] == "kb-c"][0]
        assert lazy["lazy"] is True and lazy["data"] is None
        assert "kb.panel_c" not in called, "a lazy panel must not be invoked"

    def test_include_lazy_invokes_it(self):
        panels, called, _ = _run(_contribs(), include_lazy=True)
        lazy = [p for p in panels if p["id"] == "kb-c"][0]
        assert lazy["lazy"] is False and lazy["data"] is not None
        assert "kb.panel_c" in called


class TestFailSoft:
    def test_raising_contributor_drops_out_and_is_reported(self):
        panels, _, errors = _run(_contribs(), raises={"task.panel_a"})
        assert "task-a" not in [p["id"] for p in panels]
        assert len(errors) == 1 and "task.panel_a" in errors[0]

    def test_none_means_nothing_to_show(self):
        panels, _, errors = _run(_contribs(), returns={"task.panel_a": None})
        assert "task-a" not in [p["id"] for p in panels]
        assert errors == [], "None is not an error — it is an empty state"

    def test_one_failure_does_not_take_the_board_down(self):
        panels, _, _ = _run(_contribs(), include_lazy=True, raises={"task.panel_a"})
        assert {p["id"] for p in panels} == {"rooms-b", "kb-c", "repo:panel_d"}


class TestRowShape:
    def test_limit_caps_list_data(self):
        c = [{"_app_id": "a", "method": "m", "id": "x", "limit": 2}]
        panels, _, _ = _run(c, returns={"a.m": [1, 2, 3, 4]})
        assert panels[0]["data"] == [1, 2]

    def test_limit_ignored_for_non_list(self):
        c = [{"_app_id": "a", "method": "m", "id": "x", "limit": 2}]
        panels, _, _ = _run(c, returns={"a.m": {"label": "v"}})
        assert panels[0]["data"] == {"label": "v"}

    def test_sorted_by_priority_then_id(self):
        panels, _, _ = _run(_contribs(), include_lazy=True)
        assert [p["priority"] for p in panels] == sorted(p["priority"] for p in panels)
        assert panels[0]["id"] == "rooms-b"  # priority 10

    def test_defaults_fill_in(self):
        c = [{"_app_id": "a", "method": "m"}]
        panels, _, _ = _run(c)
        p = panels[0]
        assert p["renderer"] == "plain-list" and p["priority"] == 100
        assert p["title"] == "" and p["group"] == "" and p["source"] == "a"

    def test_placeholder_and_called_rows_share_a_shape(self):
        """The two constructors must not drift — a renderer reads both."""
        lazy_only = [{"_app_id": "a", "method": "m", "id": "x", "lazy": True}]
        ph, _, _ = _run(lazy_only)
        full, _, _ = _run(lazy_only, include_lazy=True)
        assert set(ph[0]) == set(full[0])


class TestGuards:
    def test_empty_contributions(self):
        assert _run([])[0] == []

    def test_malformed_contribution_is_skipped(self):
        c = [{"id": "no-app"}, {"_app_id": "a", "id": "no-method"}]
        panels, called, _ = _run(c)
        assert panels == [] and called == []
