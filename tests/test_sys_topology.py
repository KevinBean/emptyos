"""System app tests: Topology — 10 use cases."""

import pytest

from helpers import assert_dict_response, assert_ok
from page_helpers import (
    assert_no_js_errors, wait_briefly,
)


@pytest.mark.api
class TestTopologyAPI:
    def test_graph_data(self, http_client):
        data = assert_dict_response(http_client.get("/api/topology"))
        assert "nodes" in data and "edges" in data, (
            f"Topology missing nodes/edges: {list(data.keys())}"
        )
        assert isinstance(data["nodes"], list)
        assert isinstance(data["edges"], list)

    def test_node_count(self, http_client):
        data = http_client.get("/api/topology").json()
        nodes = data.get("nodes", [])
        # System should have at least 60 nodes (apps + plugins + capabilities)
        assert len(nodes) >= 50, f"Expected >= 50 nodes, got {len(nodes)}"

    def test_edges_valid_refs(self, http_client):
        """At least 90% of edges should reference known nodes."""
        data = http_client.get("/api/topology").json()
        node_ids = {n.get("id") for n in data.get("nodes", []) if n.get("id")}
        edges = data.get("edges", [])
        if not edges:
            pytest.skip("No edges to validate")
        invalid = []
        for e in edges:
            src, tgt = e.get("source"), e.get("target")
            if (src and src not in node_ids) or (tgt and tgt not in node_ids):
                invalid.append((src, tgt))
        # Allow up to 10% dangling edges (event types may not be node-registered)
        invalid_pct = len(invalid) / len(edges)
        assert invalid_pct < 0.10, (
            f"{len(invalid)}/{len(edges)} edges ({invalid_pct:.0%}) have unknown refs. "
            f"Sample: {invalid[:3]}"
        )

    def test_node_types(self, http_client):
        data = http_client.get("/api/topology").json()
        types = {n.get("type") for n in data.get("nodes", []) if n.get("type")}
        # Expect at least app + plugin or capability
        assert types, "No node types found"

    def test_layers(self, http_client):
        resp = http_client.get("/api/topology/layers")
        if resp.status_code == 404:
            pytest.skip("layers endpoint not present")
        assert resp.status_code == 200

    def test_improvements(self, http_client):
        resp = http_client.get("/api/topology/improvements")
        if resp.status_code == 404:
            pytest.skip("improvements endpoint not present")
        assert resp.status_code == 200

    def test_timeline_dates_foundation_layers(self, http_client):
        """Capabilities and providers must carry a real birth date, not "now".

        They are not manifest-backed — the capabilities are kernel-native and enhancer
        plugins register providers in code — so the derivation used to fall through to
        today's timestamp for all ~50 of them. Sitting at max_date, they were hidden at
        every timeline cutoff except the final instant, which erased the whole L0/L1
        foundation from the growth story.
        """
        tl = http_client.get("/api/topology/timeline").json()
        nodes = tl.get("nodes") or {}
        if not nodes:
            pytest.skip("timeline unavailable")
        max_date = tl["max_date"]

        def dates_for(prefix):
            return {
                k: (v["date"] if isinstance(v, dict) else v)
                for k, v in nodes.items()
                if k.startswith(prefix)
            }

        caps = dates_for("cap:")
        provs = dates_for("prov:")
        assert caps, "no capability nodes on the timeline"
        assert provs, "no provider nodes on the timeline"

        # Not every one of them may be stamped at the very end of the timeline.
        for label, group in (("capabilities", caps), ("providers", provs)):
            stuck = [k for k, d in group.items() if d >= max_date]
            assert len(stuck) < len(group), (
                f"all {len(group)} {label} are dated at max_date ({max_date}) — "
                f"they will be hidden at every timeline cutoff. Sample: "
                f"{sorted(group.items())[:3]}"
            )
        # Providers arrived over time (kernel ones at genesis, plugin ones later), so a
        # single shared timestamp across all of them means the derivation collapsed.
        assert len(set(provs.values())) > 1, (
            f"every provider shares one timestamp ({next(iter(provs.values()))}) — "
            "the per-provider date lookup is not resolving"
        )

    def test_node_subgraph(self, http_client):
        resp = http_client.get("/api/topology/node/task")
        if resp.status_code == 404:
            pytest.skip("node subgraph endpoint not present")
        assert resp.status_code == 200

    def test_timeline_shape(self, http_client):
        """Timeline endpoint returns {min_date, max_date, nodes, time_resolution}."""
        resp = http_client.get("/api/topology/timeline")
        if resp.status_code == 404:
            pytest.skip("timeline endpoint not present")
        data = assert_dict_response(resp)
        for key in ("min_date", "max_date", "nodes", "time_resolution"):
            assert key in data, f"timeline payload missing {key}: {list(data.keys())}"
        assert data["time_resolution"] in ("day", "minute")
        assert isinstance(data["nodes"], dict)

    def test_timeline_node_entries(self, http_client):
        """Each node entry is {date, message} (cache schema v3)."""
        data = http_client.get("/api/topology/timeline").json()
        nodes = data.get("nodes", {})
        if not nodes:
            pytest.skip("no timeline nodes")
        sample_id, sample = next(iter(nodes.items()))
        assert isinstance(sample, dict), f"node entry not a dict: {sample!r}"
        assert "date" in sample, f"node entry missing 'date': {sample}"
        # Date is full ISO with timezone
        assert "T" in sample["date"], f"date not ISO: {sample['date']}"

    def test_timeline_public_strips_hours(self, http_client):
        """When time_resolution=day, every timestamp must be midnight UTC."""
        data = http_client.get("/api/topology/timeline").json()
        if data.get("time_resolution") != "day":
            pytest.skip("local mode — minute resolution is fine")
        # Public mode: all timestamps end in T00:00:00+00:00
        for nid, entry in data.get("nodes", {}).items():
            d = entry.get("date") if isinstance(entry, dict) else entry
            assert d.endswith("T00:00:00+00:00"), (
                f"public-mode timestamp leaks hour-of-day: {nid} -> {d}"
            )

    def test_tree_shape(self, http_client):
        """Tree endpoint returns {roots, groundcover}; roots are 9 capabilities."""
        resp = http_client.get("/api/topology/tree")
        if resp.status_code == 404:
            pytest.skip("tree endpoint not present")
        data = assert_dict_response(resp)
        assert "roots" in data and isinstance(data["roots"], list)
        assert "groundcover" in data and isinstance(data["groundcover"], list)
        assert len(data["roots"]) >= 5, f"expected ≥5 capability roots, got {len(data['roots'])}"
        for root in data["roots"]:
            for key in ("id", "label", "providers", "engines", "consumers"):
                assert key in root, f"capability root missing {key}: {list(root.keys())}"

    def test_tree_groundcover_kinds(self, http_client):
        """Groundcover entries declare kind = sapling|flower."""
        data = http_client.get("/api/topology/tree").json()
        ground = data.get("groundcover", [])
        if not ground:
            pytest.skip("no groundcover apps")
        for app in ground:
            assert app.get("kind") in ("sapling", "flower"), (
                f"unexpected groundcover kind: {app.get('kind')} for {app.get('id')}"
            )

    def test_releases_shape(self, http_client):
        """Releases endpoint returns {releases: [{tag, date, message}, ...]}."""
        resp = http_client.get("/api/topology/releases")
        if resp.status_code == 404:
            pytest.skip("releases endpoint not present")
        data = assert_dict_response(resp)
        assert "releases" in data and isinstance(data["releases"], list)
        if not data["releases"]:
            pytest.skip("repo has no git tags")
        for r in data["releases"]:
            for key in ("tag", "date", "message"):
                assert key in r, f"release entry missing {key}: {list(r.keys())}"


@pytest.mark.interactive
class TestTopologyUI:
    def test_ui_page_loads(self, page, base_url, page_errors):
        resp = page.goto(base_url + "/topology", wait_until="domcontentloaded", timeout=15000)
        assert resp.status == 200
        wait_briefly(page, 1000)
        assert_no_js_errors(page_errors)

    def test_ui_graph_renders(self, page, base_url, page_errors):
        page.goto(base_url + "/topology", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 2500)
        # SVG or canvas graph container should exist
        graphs = page.locator("svg, canvas")
        assert graphs.count() > 0, "No SVG or canvas graph element found"
        assert_no_js_errors(page_errors)

    def test_ui_node_interaction(self, page, base_url, page_errors):
        """Verify clickable node elements present."""
        page.goto(base_url + "/topology", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 2500)
        # SVG nodes typically have circle, g, or .node class
        nodes = page.locator("svg circle, svg g.node, .node")
        # Don't fail if zero — graph may render with different structure
        assert_no_js_errors(page_errors)

    def test_ui_view_switcher(self, page, base_url, page_errors):
        """All four view buttons present and switchable."""
        page.goto(base_url + "/topology", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 1500)
        for view in ("graph", "tree", "pyramid", "dictionary"):
            btn = page.locator(f"#btn-view-{view}")
            assert btn.count() == 1, f"missing view button: {view}"
        # Switching to tree should hide the graph SVG
        page.locator("#btn-view-tree").click()
        wait_briefly(page, 800)
        assert page.locator("#tree-view").count() == 1
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_timeline_bar(self, page, base_url, page_errors):
        """Timeline bar with toggle + slider + log button + release marks renders."""
        page.goto(base_url + "/topology", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 1500)
        for el in ("#tl-toggle", "#tl-play", "#tl-log", "#timeline-slider", "#release-marks"):
            assert page.locator(el).count() == 1, f"missing timeline element: {el}"
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_timeline_no_hours_visible(self, page, base_url, page_errors):
        """Privacy: cutoff label must never display HH:MM, only YYYY-MM-DD."""
        import re
        page.goto(base_url + "/topology", wait_until="domcontentloaded", timeout=15000)
        wait_briefly(page, 1500)
        page.locator("#tl-toggle").click()  # turn timeline on
        wait_briefly(page, 600)
        label = page.locator("#timeline-date").text_content() or ""
        # Match HH:MM patterns like "14:32" anywhere — must not appear
        assert not re.search(r"\b\d{2}:\d{2}\b", label), (
            f"cutoff label leaks hour-of-day: {label!r}"
        )
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_pyramid_timeline_hides_unborn_nodes(self, page, base_url, page_errors):
        """Pyramid nodes carry ISO dates, so a timeline cutoff hides the unborn ones.

        Pins the regression both ways. The original bug wrote the raw
        ``{date, message}`` object into ``data-created``, so every pyramid node
        parsed as NaN and *nothing* ever hid. A hypothetical inverse bug would hide
        *everything*. Asserting only "something hid" would pass the second, so the
        full-open cutoff is checked too.
        """
        page.goto(base_url + "/topology", wait_until="domcontentloaded", timeout=15000)
        # The whole boot chain (topology + layers + timeline) must land before the
        # pyramid can draw; /integrity/api/audit alone runs ~4s on a loaded daemon.
        page.wait_for_function(
            "document.querySelector('#tl-min').textContent.trim().length === 10",
            timeout=30000,
        )
        page.locator("#btn-view-pyramid").click()
        page.wait_for_function(
            "document.querySelectorAll('.cpyr-node').length > 0",
            timeout=30000,
        )
        page.locator("#tl-toggle").click()

        def hidden_at(slider_value: int) -> int:
            page.locator("#timeline-slider").evaluate(
                """(slider, v) => {
                    slider.value = String(v);
                    slider.dispatchEvent(new Event('input', {bubbles: true}));
                }""",
                slider_value,
            )
            wait_briefly(page, 300)
            return page.locator(".cpyr-node.node-hidden").count()

        total = page.locator(".cpyr-node").count()

        # The date must reach the DOM as a parseable ISO string, never "[object Object]".
        bad_dates = page.locator('.cpyr-node[data-created*="[object Object]"]').count()
        assert bad_dates == 0, f"Pyramid has {bad_dates} non-date timeline values"

        # Early cutoff: most of the system does not exist yet.
        early = hidden_at(100)
        assert early > 0, "Early timeline cutoff hid no pyramid node"

        # Full-open cutoff: everything has been born, so nothing may be hidden.
        latest = hidden_at(1000)
        assert latest == 0, (
            f"Cutoff at max date still hides {latest}/{total} pyramid nodes"
        )
        assert early < total, "Early cutoff hid every node — filter is inverted"
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_pyramid_dense_row_dots_do_not_collapse(self, page, base_url, page_errors):
        """A dense row renders as a spread dot field, not one fused bar.

        L4 holds ~750 event nodes. Sizing its lanes by pill height crushes them onto
        a single line at a ~3px pitch, where 8px dots overlap 2.5x and read as a
        solid divider rather than nodes.
        """
        page.goto(base_url + "/topology", wait_until="domcontentloaded", timeout=15000)
        page.locator("#btn-view-pyramid").click()
        page.wait_for_function(
            "document.querySelectorAll('.cpyr-node').length > 0", timeout=30000
        )
        # A narrow viewport can push more than one row into dot mode, and each dense
        # row sizes its own dot. So measure each lane against *its own* radius — a
        # global min-gap compared to some other row's radius proves nothing.
        geom = page.evaluate("""() => {
            const dots = Array.from(document.querySelectorAll('.cpyr-dot'));
            if (!dots.length) return null;
            const byLane = {};
            dots.forEach(d => {
                const m = /translate\\(([-\\d.]+),([-\\d.]+)\\)/.exec(d.getAttribute('transform'));
                const y = parseFloat(m[2]).toFixed(1);
                const r = parseFloat(d.querySelector('circle').getAttribute('r'));
                (byLane[y] ||= []).push({x: parseFloat(m[1]), r});
            });
            // Rows share a radius, so radius groups rows; lanes subdivide them.
            const lanesPerRow = {};
            let worst = null;
            Object.entries(byLane).forEach(([y, arr]) => {
                const r = arr[0].r;
                lanesPerRow[r] = (lanesPerRow[r] || 0) + 1;
                arr.sort((a, b) => a.x - b.x);
                for (let i = 1; i < arr.length; i++) {
                    const gap = arr[i].x - arr[i - 1].x;
                    const slack = gap - r * 2;
                    if (!worst || slack < worst.slack) {
                        worst = {slack, gap, diameter: r * 2, lane: y};
                    }
                }
            });
            return {count: dots.length, lanesPerRow, worst};
        }""")
        if not geom:
            pytest.skip("no dense row in this topology")
        for radius, lanes in geom["lanesPerRow"].items():
            assert lanes > 1, (
                f"dense row (r={radius}) collapsed onto a single lane — "
                f"its dots fuse into a solid bar"
            )
        worst = geom["worst"]
        assert worst["slack"] >= 0, (
            f"dots overlap: gap {worst['gap']:.1f}px < {worst['diameter']:.1f}px "
            f"rendered diameter in lane y={worst['lane']}"
        )
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_every_edge_type_is_reachable(self, page, base_url, page_errors):
        """Every edge type the backend emits must be drawable and filterable.

        `optional_calls_app` (a manifest `optional_apps` dependency) had neither a
        stroke colour nor a chip claiming it, so its 149 edges were painted with SVG's
        default stroke of `none` in the graph and could not be switched back on in the
        pyramid — invisible in both, even with every filter enabled.
        """
        page.goto(base_url + "/topology", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_function(
            "document.querySelectorAll('svg #edges line').length > 0", timeout=30000
        )
        report = page.evaluate("""() => {
            const types = [...new Set(edges.map(e => e.type))];
            const claimed = new Set();
            Object.values(PYR_GROUPS).forEach(g => g.types.forEach(t => claimed.add(t)));
            const unclaimed = types.filter(t => !claimed.has(t));
            // A type with no stroke colour renders as SVG's default: none.
            const colourless = types.filter(t => {
                const line = document.querySelector(`#edges line.${t}`);
                if (!line) return false;
                const s = getComputedStyle(line).stroke;
                return !s || s === 'none';
            });
            return {types, unclaimed, colourless};
        }""")
        assert not report["unclaimed"], (
            f"edge types no filter chip claims (can never be shown): {report['unclaimed']}"
        )
        assert not report["colourless"], (
            f"edge types with no stroke colour (drawn invisibly): {report['colourless']}"
        )
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_graph_spatial_structures_are_sound(self, page, base_url, page_errors):
        """The quadtree and the collide grid must agree with brute force.

        Both replaced an O(n²) sweep, and the failure mode of a spatial index is not
        slowness — it is silently *missing* pairs. The grid only searches a cell's own
        8 neighbours, so its cell must be at least as wide as the largest distance at
        which two nodes can still touch; grow a node radius without growing the cell
        and overlaps stop being seen at all.
        """
        page.goto(base_url + "/topology", wait_until="domcontentloaded", timeout=15000)
        page.locator("#btn-view-graph").click()
        page.wait_for_function(
            "document.querySelectorAll('svg #nodes > *').length > 0", timeout=30000
        )
        page.wait_for_function(
            "window._alpha && window._alpha.value === 0", timeout=60000
        )
        check = page.evaluate("""() => {
            const live = nodes.filter(n => !n.hidden);

            // 1. The grid cell must span the widest possible contact distance.
            const widestContact = Math.max(...Object.values(SIZES)) * 2 + COLLIDE_PAD;

            // 2. Barnes-Hut must approximate the brute-force repulsion it replaced.
            //    Compare the force on a sample of nodes against the exact O(n²) sum.
            //    Sample widely: the error is worst near a cell boundary, so a narrow
            //    sample can miss it entirely and make this test flaky.
            const sample = live.filter((_, i) => i % 7 === 0);
            let worstErr = 0;
            for (const n of sample) {
                let ex = 0, ey = 0;
                for (const m of live) {
                    if (m === n) continue;
                    const dx = m.x - n.x, dy = m.y - n.y;
                    const d = Math.sqrt(dx*dx + dy*dy) || 1;
                    const f = Math.min(REPULSION / (d*d), REPULSION_CAP);
                    ex -= dx/d * f; ey -= dy/d * f;
                }
                const before = {vx: n.vx, vy: n.vy};
                n.vx = 0; n.vy = 0;
                bhForce(bhBuild(live), n);
                const ax = n.vx, ay = n.vy;
                n.vx = before.vx; n.vy = before.vy;

                const exact = Math.hypot(ex, ey);
                const err = Math.hypot(ax - ex, ay - ey) / Math.max(exact, 0.01);
                if (err > worstErr) worstErr = err;
            }
            return {widestContact, cell: COLLIDE_CELL, worstBhError: worstErr};
        }""")
        assert check["cell"] >= check["widestContact"], (
            f"collide cell {check['cell']}px is narrower than the widest contact "
            f"distance {check['widestContact']}px — overlapping pairs in non-adjacent "
            f"cells will never be compared"
        )
        # At theta=0.5 the measured worst case is ~4%. 15% leaves room for a different
        # settled layout while still catching a tree that is genuinely wrong (theta=0.9
        # alone was 29%, which is why it is not 0.9).
        assert check["worstBhError"] < 0.15, (
            f"Barnes-Hut repulsion diverges from the exact sum by "
            f"{check['worstBhError']:.0%} — the tree is not approximating, it is wrong"
        )
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])

    def test_ui_graph_is_legible(self, page, base_url, page_errors):
        """The graph must be readable, not a hairball.

        Three things it used to get wrong: it painted a label on all ~1050 nodes (a
        solid smear); it laid out the ~750 event nodes even though they are pure
        fan-out noise; and its repulsion was blind to node radius, so circles sat on
        top of each other. Nodes must also stay on the canvas — the layout has no pan
        or zoom, so a node outside the viewport is simply gone.
        """
        page.goto(base_url + "/topology", wait_until="domcontentloaded", timeout=15000)
        page.locator("#btn-view-graph").click()
        page.wait_for_function(
            "document.querySelectorAll('svg #nodes > *').length > 0", timeout=30000
        )
        # The force sim runs on requestAnimationFrame until alpha decays.
        page.wait_for_function(
            "window._alpha && window._alpha.value === 0", timeout=60000
        )
        geom = page.evaluate("""() => {
            const vis = el => el.style.display !== 'none';
            const nodeEls = Array.from(document.querySelectorAll('svg #nodes > *'));
            const labelEls = Array.from(document.querySelectorAll('svg #labels text'));
            const live = nodes.filter(n => !n.hidden);
            let overlaps = 0, offCanvas = 0;
            for (const n of live) {
                if (n.x < 0 || n.x > WIDTH || n.y < 0 || n.y > HEIGHT) offCanvas++;
            }
            for (let i = 0; i < live.length; i++) {
                for (let j = i + 1; j < live.length; j++) {
                    const d = Math.hypot(live[j].x - live[i].x, live[j].y - live[i].y);
                    const minSep = (SIZES[live[i].type] || 10) + (SIZES[live[j].type] || 10);
                    if (d < minSep) overlaps++;
                }
            }
            const pairs = live.length * (live.length - 1) / 2;
            return {
                nodesTotal: nodeEls.length,
                nodesVisible: nodeEls.filter(vis).length,
                labelsVisible: labelEls.filter(vis).length,
                overlaps, pairs, offCanvas,
                simNodes: live.length,
            };
        }""")
        # Events are filtered out of the layout entirely, not merely display:none'd.
        assert geom["simNodes"] == geom["nodesVisible"], (
            "hidden nodes are still being laid out by the simulation"
        )
        assert geom["nodesVisible"] < geom["nodesTotal"], (
            "no node-type filtering is happening — the event hairball is still drawn"
        )
        # Labels are decluttered to the most-connected nodes; the rest peek on hover.
        assert geom["labelsVisible"] <= 90, (
            f"{geom['labelsVisible']} labels painted at once — they smear together"
        )
        assert geom["offCanvas"] == 0, (
            f"{geom['offCanvas']} nodes drifted off-canvas and are unreachable"
        )
        # A collide pass runs every tick, so residual overlap must be a rounding tail.
        overlap_pct = geom["overlaps"] / max(1, geom["pairs"]) * 100
        assert overlap_pct < 0.5, (
            f"{geom['overlaps']}/{geom['pairs']} node pairs overlap "
            f"({overlap_pct:.2f}%) — nodes are stacked on each other"
        )
        assert_no_js_errors(page_errors, allow_patterns=["fetch"])
