"""EmptyOS web — topology / graph-analysis engine + /api/topology* routes.

Extracted verbatim from ``emptyos/web/server.py`` to keep the server spine
atomic (P4 Atomic, CLAUDE.md rule 4). Owns: the full system dependency-graph
builder (``_build_topology``), the per-node creation-date timeline + git-tag
release markers, the capability-rooted tree view, Tarjan SCC app-dependency
cycle detection (``_app_dependency_cycles``), Kahn layering / critical-path /
fan-in-out / data-coupling analysis (``_analyze_layers``), and the prioritized
improvement recommender (``_compute_improvements``).

``register_topology_routes(server, kernel)`` registers the /api/topology*
endpoints and is called by ``create_server`` at the exact source position the
inline decorators previously occupied, so route registration order is
unchanged.

Do not import from ``emptyos.web.server`` (it imports us — that would cycle).
``/api/apps/clusters`` deliberately stays in server.py: it must register
before ``/api/apps/{app_id}`` and uses ``emptyos.sdk.clustering``, not this
engine.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI

if TYPE_CHECKING:
    from emptyos.kernel import Kernel


def register_topology_routes(server: FastAPI, kernel: Kernel) -> None:
    """Register the /api/topology* endpoints (moved verbatim from create_server)."""
    @server.get("/api/topology")
    async def topology():
        """Return the full system dependency graph as nodes + edges."""
        return _build_topology(kernel)

    @server.get("/api/topology/layers")
    async def topology_layers():
        """Layered architecture analysis — dependency depth, cycles, critical path."""
        return _analyze_layers(kernel)

    @server.get("/api/topology/timeline")
    async def topology_timeline():
        """Per-node creation date for the timeline scrubber.

        Order: manifest `created` override → git first-add date → mtime.
        Cached at data/apps/topology/dates.json keyed by HEAD.
        """
        # _topology_timeline shells `git` (sync); run off the loop so opening
        # /topology doesn't block every other request on git.
        return await asyncio.to_thread(_topology_timeline, kernel)

    @server.get("/api/topology/tree")
    async def topology_tree():
        """Capability-rooted tree view: 9 capabilities → providers + consuming apps."""
        # _build_topology_tree → _topology_timeline → git (sync); off the loop.
        return await asyncio.to_thread(_build_topology_tree, kernel)

    @server.get("/api/topology/releases")
    async def topology_releases():
        """Git-tag release markers for the timeline slider."""
        # _topology_releases shells `git tag` (sync); off the loop.
        return await asyncio.to_thread(_topology_releases, kernel)

    @server.get("/api/topology/node/{node_id:path}")
    async def topology_node(node_id: str):
        """Subgraph centered on a node — all direct and 2nd-degree connections.

        Returns the focused node, its neighbors, their neighbors, and all
        edges between them. Useful for understanding one app's dependencies.
        """
        topo = _build_topology(kernel)
        nodes_map = {n["id"]: n for n in topo["nodes"]}
        if node_id not in nodes_map:
            # Try prefixed: app:dashboard, cap:think, etc.
            for prefix in ("app:", "cap:", "plugin:", "service:", "engine:", "data:", "event:"):
                if prefix + node_id in nodes_map:
                    node_id = prefix + node_id
                    break
            else:
                return {"error": f"Node '{node_id}' not found"}

        # Collect 1st and 2nd degree neighbors
        degree1 = {node_id}
        for e in topo["edges"]:
            if e["source"] == node_id:
                degree1.add(e["target"])
            elif e["target"] == node_id:
                degree1.add(e["source"])

        degree2 = set(degree1)
        for e in topo["edges"]:
            if e["source"] in degree1:
                degree2.add(e["target"])
            elif e["target"] in degree1:
                degree2.add(e["source"])

        # Filter nodes and edges
        focused_nodes = [n for n in topo["nodes"] if n["id"] in degree2]
        focused_edges = [
            e for e in topo["edges"] if e["source"] in degree2 and e["target"] in degree2
        ]

        # Annotate distance from focus
        for n in focused_nodes:
            if n["id"] == node_id:
                n["_degree"] = 0
            elif n["id"] in degree1:
                n["_degree"] = 1
            else:
                n["_degree"] = 2

        return {
            "focus": node_id,
            "focus_label": nodes_map[node_id]["label"],
            "focus_type": nodes_map[node_id]["type"],
            "nodes": focused_nodes,
            "edges": focused_edges,
            "stats": {
                "total_nodes": len(focused_nodes),
                "total_edges": len(focused_edges),
                "degree1": sum(1 for n in focused_nodes if n.get("_degree") == 1),
                "degree2": sum(1 for n in focused_nodes if n.get("_degree") == 2),
            },
        }

    @server.get("/api/topology/improvements")
    async def topology_improvements():
        """Actionable improvement recommendations from topology + integrity analysis.

        Returns prioritized list of concrete fixes with file paths and commands.
        Designed to be consumed by Claude Code for automated improvement cycles.
        """
        # Runs the integrity audit (~10s of file scans, sync); off the loop, or
        # every other request waits on it.
        return await asyncio.to_thread(_compute_improvements, kernel)


def _build_topology(kernel) -> dict:
    """Build the full system graph: nodes and edges.

    Node types: app, plugin, capability, provider, event, engine, data
    Edge types: uses_capability, uses_service, provides_service,
                emits_event, listens_event, has_provider, uses_engine,
                reads_data, writes_data
    """
    nodes = []
    edges = []
    node_ids = set()

    def add_node(nid: str, ntype: str, label: str, **extra):
        if nid not in node_ids:
            node_ids.add(nid)
            nodes.append({"id": nid, "type": ntype, "label": label, **extra})

    def add_emit_edges(owner_nid: str, manifest) -> None:
        """Event emit edges for one manifest owner (an app or a plugin).

        `internal` is a MODIFIER on `emits`, not an alternative to it: a name
        listed only under `internal` produces no node and no edge, so an app
        that declares there and nowhere else is invisible to this graph.
        """
        events = manifest.provides.get("events", {})
        internal = set(events.get("internal", []))
        for evt in events.get("emits", []):
            add_node(f"event:{evt}", "event", evt)
            edges.append(
                {
                    "source": owner_nid,
                    "target": f"event:{evt}",
                    "type": "emits_event",
                    "internal": evt in internal,
                }
            )

    # --- Capabilities + Providers ---
    for cap_name, cap in kernel.capabilities.list().items():
        add_node(f"cap:{cap_name}", "capability", cap_name)
        for provider in cap.providers:
            pid = f"prov:{cap_name}:{provider.name}"
            add_node(pid, "provider", provider.name)
            edges.append(
                {
                    "source": f"cap:{cap_name}",
                    "target": pid,
                    "type": "has_provider",
                }
            )

    # --- Plugins ---
    for plugin_id, manifest in kernel.plugins.manifests.items():
        loaded = plugin_id in kernel.plugins.instances
        add_node(
            f"plugin:{plugin_id}",
            "plugin",
            manifest.name,
            loaded=loaded,
            description=manifest.description,
        )
        for svc in manifest.provides.get("services", []):
            add_node(f"service:{svc}", "service", svc)
            edges.append(
                {
                    "source": f"plugin:{plugin_id}",
                    "target": f"service:{svc}",
                    "type": "provides_service",
                }
            )
        # Plugins emit device/bridge events apps genuinely listen for —
        # `hotkey:pressed` (braindump arms a capture, command-launcher toggles
        # its window) and `tray:capture_clicked`. Without this the listener
        # edge lands on an event node with no source, so the trigger looks
        # like it comes from nowhere and every metric counting emitters
        # undercounts.
        add_emit_edges(f"plugin:{plugin_id}", manifest)

    # --- Engines ---
    for engine_id, manifest in kernel.engines.manifests.items():
        loaded = engine_id in kernel.engines.instances
        add_node(
            f"engine:{engine_id}",
            "engine",
            manifest.name,
            loaded=loaded,
            description=manifest.description,
        )
        # Engines may depend on capabilities
        for cap in manifest.requires.get("capabilities", []):
            edges.append(
                {
                    "source": f"engine:{engine_id}",
                    "target": f"cap:{cap}",
                    "type": "uses_capability",
                }
            )

    # --- Apps ---
    for app_id, manifest in kernel.apps.manifests.items():
        state = kernel.apps.state_of(app_id).value
        add_node(
            f"app:{app_id}", "app", manifest.name, state=state, description=manifest.description
        )

        requires = manifest.requires

        # Capability edges
        for cap in requires.get("capabilities", []):
            edges.append(
                {
                    "source": f"app:{app_id}",
                    "target": f"cap:{cap}",
                    "type": "uses_capability",
                }
            )

        # Service edges
        for svc in requires.get("services", []):
            edges.append(
                {
                    "source": f"app:{app_id}",
                    "target": f"service:{svc}",
                    "type": "uses_service",
                }
            )

        # Event listen edges — manifest-declared listens plus live @on_event
        # decorators on the running instance. Mixin-heavy apps (reactor)
        # register handlers in code without duplicating them in the manifest;
        # reading from the instance keeps the graph honest.
        listen_events = set(requires.get("events", []))
        listen_events.update(manifest.provides.get("events", {}).get("listens", []))
        instance = kernel.apps.instances.get(app_id)
        if instance is not None:
            try:
                for meta, _ in instance._get_decorated("_eos_event"):
                    listen_events.add(meta["type"])
            except Exception:
                pass
        for evt in listen_events:
            add_node(f"event:{evt}", "event", evt)
            edges.append(
                {
                    "source": f"event:{evt}",
                    "target": f"app:{app_id}",
                    "type": "listens_event",
                }
            )

        # Event emit edges
        add_emit_edges(f"app:{app_id}", manifest)

        # Engine edges
        for eng in requires.get("engines", []):
            eng_nid = f"engine:{eng}"
            if eng_nid not in node_ids:
                add_node(eng_nid, "engine", eng)
            edges.append(
                {
                    "source": f"app:{app_id}",
                    "target": eng_nid,
                    "type": "uses_engine",
                }
            )

        # App-to-app dependency edges
        for dep_app in requires.get("apps", []):
            edges.append(
                {
                    "source": f"app:{app_id}",
                    "target": f"app:{dep_app}",
                    "type": "calls_app",
                }
            )
        for dep_app in requires.get("optional_apps", []):
            edges.append(
                {
                    "source": f"app:{app_id}",
                    "target": f"app:{dep_app}",
                    "type": "optional_calls_app",
                }
            )

        # Contribution-slot edges — [[contributes.<target>.<slot>]] (hub
        # panels, cable calculators, tour steps, voice intents, cad
        # workspaces) declares a runtime integration with the target app.
        # Without these edges every pure contributor reads as an orphan.
        for target in (manifest.raw.get("contributes") or {}):
            if target != app_id and target in kernel.apps.manifests:
                edges.append(
                    {
                        "source": f"app:{app_id}",
                        "target": f"app:{target}",
                        "type": "contributes_to",
                    }
                )

    # --- Vault Data Nodes (from DEFAULT_PATHS) ---
    from emptyos.runtime.vault_map import DEFAULT_PATHS

    def _data_folder(path: str) -> str:
        """Extract a meaningful data folder (up to 2 levels)."""
        parts = path.replace("\\", "/").split("/")
        # Filter out template vars and file patterns
        parts = [p for p in parts if not p.startswith("{") and "*" not in p and "." not in p]
        if not parts:
            return ""
        if parts[:2] == ["30_Resources", "EmptyOS"] and len(parts) >= 3:
            return f"{parts[0]}/{parts[1]}/{parts[2]}"
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
        return parts[0]

    # Determine write vs read: apps with vault_write, vault_config for known write dirs
    write_apps = {
        "journal",
        "quick-action",
        "healing",
        "expense",
        "nutrition",
        "rooms",
        "reactor",
        "people",
    }

    for app_id, keys in DEFAULT_PATHS.items():
        for key, fallbacks in keys.items():
            primary = fallbacks[0] if fallbacks else ""
            # Handle comma-separated multi-folder values (e.g. task scan_folders)
            for segment in primary.split(","):
                folder = _data_folder(segment.strip())
                if not folder:
                    continue
                data_nid = f"data:{folder}"
                add_node(data_nid, "data", folder)
                edge_type = "writes_data" if app_id in write_apps else "reads_data"
                edges.append(
                    {
                        "source": f"app:{app_id}",
                        "target": data_nid,
                        "type": edge_type,
                    }
                )

    return {"nodes": nodes, "edges": edges}


def _topology_timeline(kernel) -> dict:
    """Compute per-node creation date with cache.

    Resolution order per node:
      1. Manifest `created` field in [app] / [plugin] / [engine] table
      2. `git log --diff-filter=A --follow --format=%aI -- <manifest.toml>` (oldest)
      3. Filesystem mtime of manifest.toml

    Derived nodes (capabilities, providers, services, events, data) inherit the
    earliest date among manifests that introduce them.
    """
    import datetime
    import json
    import subprocess

    repo_root = kernel.config.path.resolve().parent
    cache_path = kernel.config.data_dir / "apps" / "topology" / "dates.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    head = ""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except Exception:
        pass

    # Cache schema version — bump when shape changes.
    # v2: full ISO timestamps. v3: nodes are {date, message} dicts (was bare strings).
    # v4: capabilities + providers are dated from what introduced them, instead of
    #     silently falling through to today (which hid all of L0/L1 on the timeline).
    CACHE_VERSION = 4

    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("head") == head and head and cached.get("version") == CACHE_VERSION:
                return _topology_timeline_serve(
                    kernel,
                    cached.get("min_date"),
                    cached.get("max_date"),
                    cached.get("nodes", {}),
                )
        except Exception:
            pass

    def _git_first_seen(path) -> tuple[str, str] | None:
        """Returns (UTC ISO timestamp, commit subject) of the commit that first
        added `path`. Subject (`%s`) gives the birth log a real changelog feel."""
        try:
            r = subprocess.run(
                [
                    "git",
                    "log",
                    "--follow",
                    "--diff-filter=A",
                    "--format=%aI%x09%s",
                    "--",
                    str(path),
                ],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            lines = [line for line in r.stdout.strip().splitlines() if line]
            if not lines:
                return None
            parts = lines[-1].split("\t", 1)
            iso = parts[0]
            subj = parts[1] if len(parts) > 1 else ""
            try:
                dt = datetime.datetime.fromisoformat(iso)
                return (dt.astimezone(datetime.timezone.utc).isoformat(), subj)
            except Exception:
                return (iso, subj)
        except Exception:
            return None

    def _normalize(value) -> str:
        """Coerce a manifest `created` value (date-only or full ISO) to UTC ISO."""
        s = str(value)
        try:
            if "T" not in s:
                s = s + "T00:00:00+00:00"
            dt = datetime.datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return dt.astimezone(datetime.timezone.utc).isoformat()
        except Exception:
            return s

    def _date_for(manifest) -> dict:
        """Returns {date: ISO, message: str}."""
        raw = getattr(manifest, "raw", {}) or {}
        for table in ("app", "plugin", "engine"):
            section = raw.get(table)
            if isinstance(section, dict) and section.get("created"):
                return {"date": _normalize(section["created"]), "message": ""}
        manifest_dir = getattr(manifest, "path", None)
        if manifest_dir is not None:
            mfile = Path(manifest_dir) / "manifest.toml"
            if mfile.exists():
                hit = _git_first_seen(mfile)
                if hit:
                    return {"date": hit[0], "message": hit[1]}
                try:
                    return {
                        "date": datetime.datetime.fromtimestamp(
                            mfile.stat().st_mtime, tz=datetime.timezone.utc
                        ).isoformat(),
                        "message": "(uncommitted)",
                    }
                except Exception:
                    pass
        return {
            "date": datetime.datetime.now(tz=datetime.timezone.utc).isoformat(),
            "message": "",
        }

    nodes: dict[str, dict] = {}

    today_iso = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()
    today_entry = {"date": today_iso, "message": ""}

    def _earlier(a: dict, b: dict) -> dict:
        return a if a["date"] <= b["date"] else b

    # Apps
    for app_id, manifest in kernel.apps.manifests.items():
        nodes[f"app:{app_id}"] = _date_for(manifest)

    # Plugins
    for plugin_id, manifest in kernel.plugins.manifests.items():
        d = _date_for(manifest)
        nodes[f"plugin:{plugin_id}"] = d
        for svc in manifest.provides.get("services", []):
            sid = f"service:{svc}"
            nodes[sid] = _earlier(d, nodes.get(sid, d))

    # Engines
    for engine_id, manifest in kernel.engines.manifests.items():
        nodes[f"engine:{engine_id}"] = _date_for(manifest)

    # Capabilities + providers.
    #
    # Neither is manifest-backed. The 16 capabilities are kernel-native, and an
    # enhancer plugin injects its providers *in code* at connect() time (the
    # Graceful Enhancement pattern) rather than declaring them. The previous lookup
    # keyed on manifest `provides.capabilities` / `enhances` — which no plugin
    # actually writes — so the match never fired, every capability and provider
    # inherited today's date, and the entire L0/L1 foundation stayed hidden at every
    # timeline cutoff except the final instant.
    #
    # Date a provider by whatever introduced it: the plugin that registers it, or its
    # own module under emptyos/capabilities/providers/. A capability is then born with
    # its earliest provider. Anything still unattributed is kernel substrate that
    # predates the graph, so it inherits the earliest date in the system — never today.
    provider_root = Path(__file__).resolve().parent.parent / "capabilities" / "providers"

    dated = [v["date"] for v in nodes.values()]
    floor_entry = {"date": min(dated), "message": ""} if dated else today_entry

    def _provider_date(cap_name: str, provider_name: str) -> dict:
        # A plugin whose id is the provider (ollama, comfyui, edge-tts, voice-api…).
        hit = nodes.get(f"plugin:{provider_name}")
        if hit:
            return hit
        # A provider module shipped with the kernel (filesystem, human, claude_cli…).
        stem = provider_name.replace("-", "_")
        for cand in (stem, f"{stem}_{cap_name}", f"{cap_name}_{stem}"):
            src = provider_root / f"{cand}.py"
            if src.exists():
                seen = _git_first_seen(src)
                if seen:
                    return {"date": seen[0], "message": seen[1]}
        return floor_entry

    for cap_name, cap in kernel.capabilities.list().items():
        earliest = None
        for provider in cap.providers:
            pdate = _provider_date(cap_name, provider.name)
            nodes[f"prov:{cap_name}:{provider.name}"] = pdate
            earliest = pdate if earliest is None else _earlier(pdate, earliest)
        nodes[f"cap:{cap_name}"] = earliest or floor_entry

    # Events — earliest date of any emitter/listener
    event_dates: dict[str, dict] = {}
    for app_id, manifest in kernel.apps.manifests.items():
        ad = nodes.get(f"app:{app_id}", today_entry)
        for evt in manifest.provides.get("events", {}).get("emits", []) or []:
            event_dates[evt] = _earlier(event_dates.get(evt, today_entry), ad)
        for evt in manifest.requires.get("events", []) or []:
            event_dates[evt] = _earlier(event_dates.get(evt, today_entry), ad)
        for evt in manifest.provides.get("events", {}).get("listens", []) or []:
            event_dates[evt] = _earlier(event_dates.get(evt, today_entry), ad)
    for evt, d in event_dates.items():
        nodes[f"event:{evt}"] = d

    if nodes:
        all_iso = [v["date"] for v in nodes.values()]
        min_date = min(all_iso)
        max_date = max(all_iso)
    else:
        min_date = max_date = today_iso

    payload = {
        "version": CACHE_VERSION,
        "head": head,
        "min_date": min_date,
        "max_date": max_date,
        "nodes": nodes,
    }
    try:
        cache_path.write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        pass

    return _topology_timeline_serve(kernel, min_date, max_date, nodes)


def _topology_timeline_serve(kernel, min_date: str, max_date: str, nodes: dict) -> dict:
    """Apply public-mode privacy filter on the way out.

    On `network.mode = "public"` or `demo.enabled`, strip time-of-day from every
    timestamp so commit hours never leak. Cache stays full-fidelity so a private
    machine that later opens to public has no cache invalidation needed.
    """
    public_mode = kernel.config.get("network.mode") == "public" or bool(
        kernel.config.get("demo.enabled")
    )
    if public_mode:

        def _to_day(iso: str) -> str:
            return (iso[:10] + "T00:00:00+00:00") if iso else iso

        nodes = {
            k: {"date": _to_day(v["date"]), "message": v.get("message", "")}
            for k, v in nodes.items()
        }
        min_date = _to_day(min_date)
        max_date = _to_day(max_date)
    return {
        "min_date": min_date,
        "max_date": max_date,
        "nodes": nodes,
        "time_resolution": "day" if public_mode else "minute",
    }


def _topology_releases(kernel) -> dict:
    """Return git tags as release markers: [{tag, date, message}, ...] in UTC ISO."""
    import datetime
    import subprocess

    repo_root = kernel.config.path.resolve().parent
    try:
        r = subprocess.run(
            [
                "git",
                "tag",
                "--sort=creatordate",
                "--format=%(refname:short)|%(creatordate:iso-strict)|%(subject)",
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except Exception:
        return {"releases": []}

    public_mode = kernel.config.get("network.mode") == "public" or bool(
        kernel.config.get("demo.enabled")
    )
    releases = []
    for line in r.stdout.strip().splitlines():
        parts = line.split("|", 2)
        if len(parts) < 2:
            continue
        tag, iso = parts[0].strip(), parts[1].strip()
        msg = parts[2].strip() if len(parts) > 2 else ""
        try:
            dt = datetime.datetime.fromisoformat(iso)
            iso_utc = dt.astimezone(datetime.timezone.utc).isoformat()
        except Exception:
            iso_utc = iso
        if public_mode and iso_utc:
            iso_utc = iso_utc[:10] + "T00:00:00+00:00"
        releases.append({"tag": tag, "date": iso_utc, "message": msg})
    return {"releases": releases, "time_resolution": "day" if public_mode else "minute"}


def _build_topology_tree(kernel) -> dict:
    """Capability-rooted tree: 9 capabilities → providers + consuming apps.

    Apps that declare no capabilities show up in `groundcover` rather than on
    a branch — rendered as wildflowers / grass at the base of the tree.
    """
    raw = _topology_timeline(kernel).get("nodes", {})
    timeline = {k: (v.get("date") if isinstance(v, dict) else v) for k, v in raw.items()}
    roots = []
    # Build app-by-capability index
    cap_consumers: dict[str, list[dict]] = {}
    rooted_apps: set[str] = set()
    for app_id, manifest in kernel.apps.manifests.items():
        caps = manifest.requires.get("capabilities", []) or []
        for cap in caps:
            cap_consumers.setdefault(cap, []).append(
                {
                    "id": f"app:{app_id}",
                    "label": manifest.name,
                    "type": "app",
                    "created": timeline.get(f"app:{app_id}"),
                    "description": manifest.description,
                }
            )
            rooted_apps.add(app_id)
    # Engines that depend on a capability appear under it as enhancers
    cap_engines: dict[str, list[dict]] = {}
    for engine_id, manifest in kernel.engines.manifests.items():
        for cap in manifest.requires.get("capabilities", []) or []:
            cap_engines.setdefault(cap, []).append(
                {
                    "id": f"engine:{engine_id}",
                    "label": manifest.name,
                    "type": "engine",
                    "created": timeline.get(f"engine:{engine_id}"),
                }
            )

    for cap_name, cap in kernel.capabilities.list().items():
        cap_id = f"cap:{cap_name}"
        providers = []
        for provider in cap.providers:
            pid = f"prov:{cap_name}:{provider.name}"
            providers.append(
                {
                    "id": pid,
                    "label": provider.name,
                    "type": "provider",
                    "created": timeline.get(pid),
                    "is_cloud": getattr(provider, "is_cloud", False),
                    "auth_mode": getattr(provider, "auth_mode", "") or "",
                }
            )
        consumers = sorted(
            cap_consumers.get(cap_name, []),
            key=lambda n: (n.get("created") or "9999", n["label"]),
        )
        engines = sorted(
            cap_engines.get(cap_name, []),
            key=lambda n: (n.get("created") or "9999", n["label"]),
        )
        roots.append(
            {
                "id": cap_id,
                "label": cap_name,
                "type": "capability",
                "created": timeline.get(cap_id),
                "providers": providers,
                "engines": engines,
                "consumers": consumers,
            }
        )
    # Order roots by total node count desc (richest capability first), then alpha
    roots.sort(
        key=lambda r: (-(len(r["providers"]) + len(r["consumers"]) + len(r["engines"])), r["label"])
    )

    # Groundcover: apps that declare no capabilities. They live in the soil at
    # the base of the tree as flowers / grass, with their own roots — they're
    # not parasitic on any branch.  An app counts as "kind" = "engine-bound" if
    # it requires at least one engine, otherwise "rootless".  The frontend uses
    # this to draw saplings (engine-bound) vs wildflowers (rootless).
    groundcover = []
    for app_id, manifest in kernel.apps.manifests.items():
        if app_id in rooted_apps:
            continue
        engines_req = manifest.requires.get("engines", []) or []
        kind = "sapling" if engines_req else "flower"
        groundcover.append(
            {
                "id": f"app:{app_id}",
                "label": manifest.name,
                "type": "app",
                "kind": kind,
                "created": timeline.get(f"app:{app_id}"),
                "description": manifest.description,
            }
        )
    groundcover.sort(key=lambda n: (n.get("created") or "9999", n["label"]))

    return {"roots": roots, "groundcover": groundcover}


def _app_dependency_cycles(kernel, nodes: dict[str, dict], edges: list[dict]) -> list[dict]:
    """Return exact app dependency cycles via Tarjan SCC detection.

    Kahn leftovers include apps that merely depend on a cycle. SCCs keep the
    improvement list honest and let intentional collaboration loops declare a
    shared architecture label in their manifests.
    """
    app_ids = [nid for nid, node in nodes.items() if node["type"] == "app"]
    graph, _ = _dependency_lists(app_ids, edges, {"calls_app"})
    comp = _strongly_connected_components(app_ids, graph)
    members: dict[int, list[str]] = {}
    for nid, cid in comp.items():
        members.setdefault(cid, []).append(nid)
    components = sorted(
        sorted(group)
        for group in members.values()
        if len(group) > 1 or group[0] in graph[group[0]]
    )

    cycles = []
    for component in components:
        labels = []
        for node_id in component:
            manifest = kernel.apps.manifests.get(node_id.replace("app:", ""))
            labels.append(
                manifest.raw.get("architecture", {}).get("dependency_cycle", "") if manifest else ""
            )
        intentional = bool(labels) and all(labels) and len(set(labels)) == 1
        cycles.append(
            {
                "nodes": component,
                "description": f"{len(component)} apps in dependency cycle",
                "intentional": intentional,
                "label": labels[0] if intentional else "",
            }
        )
    return cycles


# Edges that mean "source depends on target" for fan-in / fan-out, data
# coupling and the orphan reading.
STRUCTURAL_EDGES = frozenset(
    {
        "uses_capability",
        "uses_service",
        "uses_engine",
        "calls_app",
        "optional_calls_app",
        "contributes_to",
        "has_provider",
        "provides_service",
        "reads_data",
        "writes_data",
    }
)
# The depth sort leaves `contributes_to` out. A contribution (a hub panel, a
# calculator in a suite, a section of a composite report) is a registration with its
# host, not a dependency on it — the edge exists so a pure contributor does not
# read as an orphan. Sorting on it pairs every contributor with the host's
# `optional_calls_app` edge back to it, which welded hub and 44 of its panel
# apps into one depth (measured 2026-10-02); without it the host sits above
# what it composes.
LAYER_SORT_EDGES = STRUCTURAL_EDGES - {"contributes_to"}


def _dependency_lists(
    node_ids, edges: list[dict], edge_types, skip=None
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """``(deps, reverse_deps)`` over the edges whose type is in ``edge_types``.

    ``deps[src]`` lists what ``src`` depends on; ``reverse_deps[tgt]`` lists
    who depends on ``tgt``. Edges touching an unknown node are dropped, and
    ``skip(edge)`` may veto one.
    """
    deps: dict[str, list[str]] = {nid: [] for nid in node_ids}
    reverse_deps: dict[str, list[str]] = {nid: [] for nid in node_ids}
    for e in edges:
        if e["type"] not in edge_types or (skip is not None and skip(e)):
            continue
        src, tgt = e["source"], e["target"]
        if src in deps and tgt in deps:
            deps[src].append(tgt)
            reverse_deps[tgt].append(src)
    return deps, reverse_deps


def _strongly_connected_components(order: list[str], deps: dict[str, list[str]]) -> dict[str, int]:
    """Tarjan's SCC over ``deps`` (node -> what it depends on), iterative.

    Iterative as cheap insurance: the live graph is ~1300 nodes, so a chain
    deeper than the default recursion limit is possible in principle, though
    the measured depth is under 20. Returns node -> component id. Every node
    named in ``order`` or reached from one gets an id; a node outside any
    cycle is a component of one.
    """
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    comp: dict[str, int] = {}
    counter = 0
    for root in order:
        if root in index:
            continue
        index[root] = low[root] = len(index)
        stack.append(root)
        on_stack.add(root)
        work = [(root, iter(deps.get(root, [])))]
        while work:
            node, it = work[-1]
            descended = False
            for nxt in it:
                if nxt not in index:
                    index[nxt] = low[nxt] = len(index)
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(deps.get(nxt, []))))
                    descended = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            if descended:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    comp[member] = counter
                    if member == node:
                        break
                counter += 1
    return comp


def _layer_nodes(
    node_types: dict[str, str],
    deps: dict[str, list[str]],
    fixed_layers: dict[str, int],
    app_base_layer: int = 4,
) -> tuple[dict[str, int], dict[str, int], dict[int, list[str]]]:
    """Assign every node a layer: ``max(layer of its dependencies) + 1``.

    Works on the condensation graph, so a node inside a dependency cycle is
    still placed — every member of a strongly connected group shares one
    layer, computed from what the *group* depends on. The previous Kahn sort
    could never drain a node in a cycle, and the hub-panel / calculator-suite
    pattern (surface ``optional_calls_app`` atom, atom ``contributes_to``
    surface) put 36 such pairs in the live graph, which chained into a few
    large groups, so 170 of 237 apps silently vanished from the layers view
    (2026-10-02).

    Nodes in ``fixed_layers`` keep that layer. App and event nodes take their
    depth from app dependencies only and never sit below ``app_base_layer``;
    other nodes take depth from every dependency and bottom out at 0. A
    dependency on a node absent from ``node_types`` is ignored.

    Returns ``(layer, comp, members)``: node -> layer, node -> component id,
    component id -> its nodes.
    """
    from collections import deque

    deps = {nid: [t for t in deps.get(nid, []) if t in node_types] for nid in node_types}
    comp = _strongly_connected_components(list(node_types), deps)
    members: dict[int, list[str]] = {}
    for nid, cid in comp.items():
        members.setdefault(cid, []).append(nid)
    comp_deps: dict[int, set[int]] = {cid: set() for cid in members}
    for nid, targets in deps.items():
        for tgt in targets:
            if comp[nid] != comp[tgt]:
                comp_deps[comp[nid]].add(comp[tgt])
    comp_rdeps: dict[int, list[int]] = {cid: [] for cid in members}
    for cid, targets in comp_deps.items():
        for tgt in targets:
            comp_rdeps[tgt].append(cid)
    has_app = {cid: any(node_types[m] == "app" for m in group) for cid, group in members.items()}

    # The condensation is a DAG, so this sort drains it completely.
    pending = {cid: len(targets) for cid, targets in comp_deps.items()}
    queue = deque(cid for cid, n in pending.items() if n == 0)
    comp_layer: dict[int, int] = {}
    while queue:
        cid = queue.popleft()
        group = members[cid]
        if all(m in fixed_layers for m in group):
            comp_layer[cid] = max(fixed_layers[m] for m in group)
        elif any(node_types[m] in ("app", "event") for m in group):
            app_dep_layers = [comp_layer[d] for d in comp_deps[cid] if has_app[d]]
            comp_layer[cid] = max(app_dep_layers) + 1 if app_dep_layers else app_base_layer
        else:
            dep_layers = [comp_layer[d] for d in comp_deps[cid]]
            comp_layer[cid] = max(dep_layers) + 1 if dep_layers else 0
        for dependent in comp_rdeps[cid]:
            pending[dependent] -= 1
            if pending[dependent] == 0:
                queue.append(dependent)
    layer = {nid: comp_layer[comp[nid]] for nid in node_types}
    return layer, comp, members


def _layer_widths(app_layers: list[int], name_for) -> dict[str, int]:
    """Count apps per *named* layer, in layer order.

    Several depths share one name (every depth past "Aggregator" is a
    "Composition" app), so the counts must be summed per name — a
    ``{name_for(lv): count}`` comprehension keeps only the last depth's
    count and under-reports the deepest band. Measured 2026-10-02: 166 of
    237 apps once cycles were placed and depths ran to 12.
    """
    widths: dict[str, int] = {}
    for lv in sorted(app_layers):
        name = name_for(lv)
        widths[name] = widths.get(name, 0) + 1
    return widths


def _analyze_layers(kernel) -> dict:
    """Compute dependency layers, detect cycles, find critical path.

    Uses the topology graph to assign each node a depth (layer) via
    topological sort.  Layer = max(depth of dependencies) + 1.
    Infrastructure nodes get fixed layers (0=providers, 1=capabilities,
    2=plugins/services, 3=engines).  App layers are computed from deps.
    """
    topo = _build_topology(kernel)
    nodes = {n["id"]: n for n in topo["nodes"]}
    edges = topo["edges"]

    def _intentional_cycle_label(node_id: str) -> str:
        if not node_id.startswith("app:"):
            return ""
        manifest = kernel.apps.manifests.get(node_id.replace("app:", ""))
        return manifest.raw.get("architecture", {}).get("dependency_cycle", "") if manifest else ""

    def _declared_loop(e: dict) -> bool:
        # A declared collaboration loop (`[architecture] dependency_cycle` on
        # both apps) is real topology but not a hierarchy edge: it is reported
        # under `cycles` as intentional and kept out of the depth sort, so its
        # members are not condensed into a `cycle_groups` entry.
        if e["type"] != "calls_app":
            return False
        label = _intentional_cycle_label(e["source"])
        return bool(label) and label == _intentional_cycle_label(e["target"])

    # Every structural edge counts for fan-in / fan-out, data coupling and
    # the orphan reading; the depth sort drops `contributes_to` (see
    # LAYER_SORT_EDGES) and declared loops.
    deps, reverse_deps = _dependency_lists(nodes, edges, STRUCTURAL_EDGES)
    sort_deps, _ = _dependency_lists(nodes, edges, LAYER_SORT_EDGES, skip=_declared_loop)

    # --- Fixed layers for infrastructure ---
    fixed_layers = {}
    for nid, node in nodes.items():
        if node["type"] == "provider":
            fixed_layers[nid] = 0
        elif node["type"] == "capability":
            fixed_layers[nid] = 1
        elif node["type"] in ("plugin", "service"):
            fixed_layers[nid] = 2
        elif node["type"] == "engine":
            fixed_layers[nid] = 3
        elif node["type"] == "data":
            fixed_layers[nid] = 3  # same level as engines (infrastructure)

    cycles = _app_dependency_cycles(kernel, nodes, edges)

    # --- Compute layers over the condensation graph ---
    # Infrastructure (providers, capabilities, plugins, engines) gets fixed layers.
    # Apps always start at layer 4+ so they never collide with infrastructure.
    # Every node gets a layer, cycles included — see `_layer_nodes`.
    node_types = {nid: node["type"] for nid, node in nodes.items()}
    layer, comp, members = _layer_nodes(node_types, sort_deps, fixed_layers)
    cycle_groups = sorted(
        (
            {"size": len(group), "layer": layer[group[0]], "members": sorted(group)}
            for group in members.values()
            if len(group) > 1
        ),
        key=lambda g: (-g["size"], g["members"]),
    )

    # --- Assign layer names ---
    max_layer = max(layer.values()) if layer else 4

    def _layer_name(lv: int) -> str:
        fixed = {0: "Providers", 1: "Capabilities", 2: "Plugins & Services", 3: "Engines & Data"}
        if lv in fixed:
            return fixed[lv]
        depth = lv - 4
        if depth == 0:
            return "Base Apps"
        if depth == 1:
            return "Single-Dep Apps"
        if depth == 2:
            return "Aggregator Apps"
        return "Composition Apps"

    # --- Build layered output ---
    layers_out: dict[int, dict] = {}
    for nid, lv in layer.items():
        if lv not in layers_out:
            layers_out[lv] = {"level": lv, "name": _layer_name(lv), "nodes": []}
        node = dict(nodes[nid])
        node["layer"] = lv
        node["fan_in"] = len(reverse_deps.get(nid, []))
        node["fan_out"] = len(deps.get(nid, []))
        layers_out[lv]["nodes"].append(node)

    # Sort layers and nodes within
    for lv_data in layers_out.values():
        lv_data["nodes"].sort(key=lambda n: (-n["fan_in"], n["label"]))
        lv_data["width"] = len(lv_data["nodes"])

    # --- Critical path (longest chain) ---
    # BFS from each leaf to find the longest path
    longest_path: list[str] = []
    memo: dict[str, list[str]] = {}

    def _longest_from(nid: str) -> list[str]:
        if nid in memo:
            return memo[nid]
        best: list[str] = []
        for dep in sort_deps.get(nid, []):
            if comp[dep] != comp[nid]:  # never walk inside a cycle group
                candidate = _longest_from(dep)
                if len(candidate) > len(best):
                    best = candidate
        memo[nid] = [nid] + best
        return memo[nid]

    for nid in nodes:
        path = _longest_from(nid)
        if len(path) > len(longest_path):
            longest_path = path

    critical_path = [
        {
            "id": nid,
            "label": nodes[nid]["label"],
            "type": nodes[nid]["type"],
            "layer": layer.get(nid, -1),
        }
        for nid in longest_path
    ]

    # --- Fan-in/fan-out rankings (top 10) ---
    fan_in_ranking = sorted(
        [(nid, len(reverse_deps.get(nid, []))) for nid in nodes if nodes[nid]["type"] == "app"],
        key=lambda x: -x[1],
    )[:10]
    fan_out_ranking = sorted(
        [(nid, len(deps.get(nid, []))) for nid in nodes if nodes[nid]["type"] == "app"],
        key=lambda x: -x[1],
    )[:10]

    # --- Summary stats ---
    layer_widths = _layer_widths(
        [layer[nid] for nid in nodes if nodes[nid]["type"] == "app"], _layer_name
    )

    # --- Data coupling analysis ---
    data_coupling = []
    for nid, node in nodes.items():
        if node["type"] != "data":
            continue
        consumers = reverse_deps.get(nid, [])
        if not consumers:
            continue
        readers = [
            c
            for c in consumers
            if any(
                e["source"] == c and e["target"] == nid and e["type"] == "reads_data" for e in edges
            )
        ]
        writers = [
            c
            for c in consumers
            if any(
                e["source"] == c and e["target"] == nid and e["type"] == "writes_data"
                for e in edges
            )
        ]
        reader_names = sorted(set(nodes[c]["label"] for c in readers if c in nodes))
        writer_names = sorted(set(nodes[c]["label"] for c in writers if c in nodes))
        unique_apps = len(set(readers) | set(writers))
        data_coupling.append(
            {
                "folder": node["label"],
                "total_apps": unique_apps,
                "readers": reader_names,
                "writers": writer_names,
                "coupling_risk": "high"
                if unique_apps >= 5
                else "medium"
                if unique_apps >= 3
                else "low",
            }
        )
    data_coupling.sort(key=lambda x: -x["total_apps"])

    return {
        "layers": [layers_out[lv] for lv in sorted(layers_out.keys())],
        "cycles": cycles,
        "critical_path": {
            "length": len(critical_path),
            "path": critical_path,
        },
        "data_coupling": data_coupling,
        "stats": {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "max_depth": max_layer,
            "layer_widths": layer_widths,
            # Apps that depend on each other (directly or through a chain) share
            # one layer; this is the list of those groups, largest first. It is
            # computed over LAYER_SORT_EDGES, so it sees `optional_calls_app`
            # loops that `cycles` (calls_app only) does not, and omits a
            # declared loop that `cycles` reports as intentional.
            "cycle_groups": cycle_groups,
            "most_depended_on": [
                {"id": nid, "label": nodes[nid]["label"], "fan_in": fi}
                for nid, fi in fan_in_ranking
            ],
            "most_dependencies": [
                {"id": nid, "label": nodes[nid]["label"], "fan_out": fo}
                for nid, fo in fan_out_ranking
            ],
        },
    }


def _app_rel_dir(kernel, app_id: str) -> str:
    """Repo-relative POSIX dir of an app, e.g. ``apps/extension/dev/repo``.

    Apps live under track folders, so ``apps/<id>`` is wrong for nearly all of
    them. Anchored on the ``apps`` ancestor rather than the config dir, which
    on a sandbox member is ``sandbox-900N/`` and not the repo.
    """
    manifest = kernel.apps.manifests.get(app_id)
    path = getattr(manifest, "path", None)
    if path:
        path = Path(path)
        for ancestor in path.parents:
            if ancestor.name == "apps":
                return path.relative_to(ancestor.parent).as_posix()
    return f"apps/{app_id}"


def _compute_improvements(kernel) -> dict:
    """Analyze topology + integrity and return prioritized improvement actions.

    Synchronous on purpose: the route runs it in a worker thread.

    Each improvement is a concrete, actionable item with:
    - category (integrity, topology, data, event)
    - priority (critical, high, medium, low)
    - description (what's wrong)
    - action (what to do)
    - files (which files to change)
    """

    improvements = []
    _audit = None  # cache for reuse by verb health section

    def _integrity_action(violation: str, fallback: str) -> str:
        if violation.startswith("Backend monolith: "):
            app = violation.removeprefix("Backend monolith: ").split(" (", 1)[0]
            return f"Decompose {app} into a small app.py spine plus focused helper modules"
        if violation.startswith("Frontend monolith: "):
            page = violation.removeprefix("Frontend monolith: ").split(" (", 1)[0]
            return f"Split {page} into focused page modules or shared frontend helpers"
        if violation.startswith("Undecomposed: "):
            app = violation.removeprefix("Undecomposed: ").split(" (", 1)[0]
            return f"Extract focused helper modules from {app}"
        if violation.startswith("Hardcoded path: "):
            return f"Migrate {violation.removeprefix('Hardcoded path: ')} to vault_config()"
        return fallback

    # --- Integrity dimension gaps ---
    try:
        integrity_app = kernel.apps.instances.get("integrity")
        if integrity_app:
            audit = integrity_app._run_audit()
            _audit = audit
            for dim_name, dim in audit.get("dimensions", {}).items():
                if dim["score"] < 10:
                    gap = 10 - dim["score"]
                    priority = "high" if gap >= 3 else "medium" if gap >= 2 else "low"
                    for violation in dim.get("violations", []):
                        improvements.append(
                            {
                                "category": "integrity",
                                "dimension": dim_name,
                                "priority": priority,
                                "score": f"{dim['score']}/10",
                                "description": violation,
                                "action": _integrity_action(
                                    violation, dim.get("growth_signal", "")
                                ),
                            }
                        )
    except Exception:
        pass

    # --- Topology: cycles ---
    layers = _analyze_layers(kernel)
    for cycle in layers.get("cycles", []):
        if cycle.get("intentional"):
            continue
        improvements.append(
            {
                "category": "topology",
                "priority": "critical",
                "description": cycle["description"],
                "action": f"Break dependency cycle between: {', '.join(cycle['nodes'][:5])}",
                "files": [
                    f"{_app_rel_dir(kernel, nid.replace('app:', ''))}/manifest.toml"
                    for nid in cycle["nodes"][:5]
                ],
            }
        )

    # --- Topology: monolith apps ---
    apps_dir = kernel.config.path.parent / "apps"
    for app_id, _m in kernel.apps.manifests.items():
        # Use the manifest's real dir (apps live under track folders now), not
        # a flat apps/<id> assumption.
        app_py = (_m.path if getattr(_m, "path", None) else apps_dir / app_id) / "app.py"
        if app_py.exists():
            try:
                lines = len(app_py.read_text(encoding="utf-8", errors="ignore").split("\n"))
                if lines > 1200:
                    improvements.append(
                        {
                            "category": "topology",
                            "priority": "medium",
                            "description": f"{app_id} is {lines} lines — monolith risk",
                            "action": "Decompose into app.py + extended.py (like briefing, projects pattern)",
                            "files": [f"{_app_rel_dir(kernel, app_id)}/app.py"],
                        }
                    )
            except Exception:
                pass

    # --- Data coupling: high-risk shared folders ---
    for dc in layers.get("data_coupling", []):
        if dc["coupling_risk"] == "high" and dc["total_apps"] >= 5:
            readers_list = ", ".join(dc["readers"])
            writers_list = ", ".join(dc["writers"])
            if dc["writers"]:
                description = (
                    f"{dc['folder']} shared by {dc['total_apps']} apps - "
                    f"{readers_list} read data also written by {writers_list}"
                )
                action = (
                    "Confirm the writer app owns this collection; if so, add a "
                    "read method and migrate direct readers to call_app()."
                )
            else:
                description = (
                    f"{dc['folder']} shared by {dc['total_apps']} read-only apps "
                    f"with no declared owner: {readers_list}"
                )
                action = (
                    "Refine vault-map paths or declare an owning app before centralizing access."
                )
            improvements.append(
                {
                    "category": "data",
                    "priority": "medium",
                    "description": description,
                    "action": action,
                }
            )

    # --- Events: unheard events from important apps ---
    topo = _build_topology(kernel)
    emitted = {}
    listened = set()
    for e in topo["edges"]:
        if e["type"] == "emits_event" and not e.get("internal"):
            emitted[e["target"]] = e["source"]
        elif e["type"] == "listens_event":
            listened.add(e["source"])
    unheard = {evt: src for evt, src in emitted.items() if evt not in listened}
    if unheard:
        # Group by source app
        by_app = {}
        for evt, src in unheard.items():
            by_app.setdefault(src, []).append(evt.replace("event:", ""))
        for src, evts in sorted(by_app.items(), key=lambda x: -len(x[1])):
            if len(evts) >= 3:
                # Emitters are apps or plugins (add_emit_edges), and plugins sit flat under plugins/.
                kind, _, app_id = src.partition(":")
                source_dir = f"plugins/{app_id}" if kind == "plugin" else _app_rel_dir(kernel, app_id)
                improvements.append(
                    {
                        "category": "event",
                        # medium, not low: an unheard event stream is a wiring
                        # gap, and this manifest-based count UNDERCOUNTS — most
                        # self.emit() calls are never declared in manifests.
                        # scripts/check_event_wiring.py is the code-level truth.
                        "priority": "medium",
                        "description": (
                            f"{app_id} emits {len(evts)} unheard events "
                            f"(manifest-declared only — code-level count is higher; "
                            f"run scripts/check_event_wiring.py): {', '.join(evts[:5])}"
                        ),
                        "action": "Wire events into reactor for logging/journal ripple, or remove unused emits",
                        "files": [
                            f"{source_dir}/manifest.toml",
                            f"{_app_rel_dir(kernel, 'reactor')}/app.py",
                        ],
                    }
                )

    # No "app has no pages/" item: auto-UI is the default and pages/ only
    # overrides it (CLAUDE.md Dev Rule 7); integrity P6 already lists auto-UI
    # apps as a growth signal.

    # --- Six Verbs health (唯识 metabolic cycle) ---
    # Reuse the audit already computed above — no re-scan needed
    verb_health = {}
    try:
        if _audit:
            verb_dim = _audit.get("dimensions", {}).get("P10 Six Verbs", {})
            verb_health = verb_dim.get("details", {}).get("verbs", {})
            # Add improvements for weak verbs
            for verb_id, vdata in verb_health.items():
                if vdata["points"] < 4:
                    missing = [k for k, v in vdata["layers"].items() if not v]
                    improvements.append(
                        {
                            "category": "verb",
                            "priority": "high" if vdata["points"] <= 2 else "medium",
                            "description": f"Weak lifecycle verb: {vdata['label']} ({vdata['points']}/6)",
                            "action": f"Add missing layers: {', '.join(missing)}",
                            "verb": verb_id,
                            "layers": vdata["layers"],
                        }
                    )
    except Exception:
        pass

    # --- Sort by priority ---
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    improvements.sort(key=lambda x: priority_order.get(x.get("priority", "low"), 9))

    return {
        "total": len(improvements),
        "by_priority": {
            p: sum(1 for i in improvements if i.get("priority") == p)
            for p in ("critical", "high", "medium", "low")
        },
        "improvements": improvements,
        "verb_health": verb_health,
    }
