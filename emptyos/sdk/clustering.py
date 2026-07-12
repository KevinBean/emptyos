"""Auto-clustering — organize apps by their dependency graph.

Builds a weighted graph from manifests, runs label propagation,
auto-names clusters. No external dependencies.

Lived at emptyos/web/clustering.py until 2026-06-11; promoted to the SDK when
app-analytics became the second consumer (home screen / server.py was the
first) — rule 9. emptyos/web/clustering.py remains as a re-export shim.
Pure module: manifests in, cluster dicts out — no kernel boot, no I/O.
"""

from __future__ import annotations

import random
from collections import defaultdict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from emptyos.kernel.app_loader import AppManifest  # noqa: F401 — annotations only

# Edge weights — stronger = closer
W_CALL_APP = 5
W_EVENT_FLOW = 4
W_OPTIONAL_APP = 3  # optional_apps — soft integration with graceful fallback
W_CONTRIBUTES = 3  # [[contributes.<target>.<slot>]] — app extends another's surface
W_SHARED_CONNECTOR = 3
W_SHARED_REQ_APP = 2
W_RARE_CAP = 2  # speak, listen, draw — distinguishing
W_SHARED_CAPS = 0.5  # common caps (think, read, write, search) — weak

# Capabilities that almost every app has — not useful for clustering
COMMON_CAPS = {"think", "read", "write", "search"}

# Apps that aggregate many others — their call_app edges are too broad for clustering.
# They connect to most of the graph, causing label propagation to converge to one cluster.
AGGREGATOR_APPS = {"assistant", "staff", "hub", "reactor"}
AGGREGATOR_CALL_WEIGHT = 1  # weak signal instead of W_CALL_APP=5

# Contribution targets nearly every app extends (hub panels, tour steps, voice
# intents) — edges to them connect everything to everything, same mega-cluster
# failure as aggregator call_app edges. Skip them; keep domain-specific slots
# (design-package sections, dictionary addons, cable satellites).
BROAD_CONTRIB_TARGETS = AGGREGATOR_APPS | {"tour", "voice-assistant"}

MAX_CLUSTER_SIZE = 10
MIN_CLUSTER_SIZE = 2

# Cluster naming rules (checked in order)
CLUSTER_HINTS = [
    (
        lambda ids, ms: _count_in(ids, ("hub", "reactor", "assistant", "staff")) >= len(ids) * 0.5,
        "🔄",
        "Aggregators",
    ),
    (
        lambda ids, ms: (
            _count_in(ids, ("english", "speaking", "shadowing", "voice-review", "tts", "lessons"))
            >= 2
        ),
        "🎤",
        "Voice & English",
    ),
    (lambda ids, ms: any("interview" in i for i in ids), "💼", "Career"),
    (
        lambda ids, ms: any(_has_connector(ms[i], "comfyui") for i in ids if i in ms),
        "🎨",
        "Creative",
    ),
    (
        lambda ids, ms: _count_in(ids, ("healing", "nutrition", "meditation", "divination")) >= 2,
        "🧘",
        "Wellness",
    ),
    (
        lambda ids, ms: (
            _count_in(
                ids,
                (
                    "settings",
                    "billing",
                    "app-analytics",
                    "app-gen",
                    "reactor",
                    "system-log",
                    "run",
                    "git",
                    "tmpl",
                    "note",
                    "quick-action",
                ),
            )
            >= len(ids) * 0.4
        ),
        "⚙️",
        "System",
    ),
    (
        lambda ids, ms: (
            _count_in(
                ids,
                (
                    "expense",
                    "focus",
                    "journal",
                    "briefing",
                    "tracker",
                    "contacts",
                    "items",
                    "places",
                    "daily-brief",
                    "nutrition",
                ),
            )
            >= 2
        ),
        "📊",
        "Life",
    ),
    (
        lambda ids, ms: (
            _count_in(ids, ("search", "dictionary", "media", "rooms", "assistant", "model-bench"))
            >= 2
        ),
        "🧠",
        "Knowledge",
    ),
    (lambda ids, ms: _count_in(ids, ("music-studio", "tts", "podcast")) >= 2, "🎵", "Music"),
    (
        lambda ids, ms: _count_in(ids, ("link", "app-analytics", "projects", "timeline")) >= 2,
        "📂",
        "Vault",
    ),
]


def _count_in(ids: list[str], targets: tuple) -> int:
    return sum(1 for i in ids if i in targets)


def _has_connector(m: AppManifest, name: str) -> bool:
    return name in m.requires.get("connectors", [])


def _caps_set(m: AppManifest) -> set[str]:
    return set(m.requires.get("capabilities", []))


def _rare_caps(m: AppManifest) -> set[str]:
    return _caps_set(m) - COMMON_CAPS


def build_graph(manifests: dict[str, AppManifest]) -> dict[str, dict[str, float]]:
    """Build weighted adjacency from manifests."""
    graph: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    for aid, m in manifests.items():
        # call_app edges — aggregators get weak weight to avoid mega-clusters
        w = AGGREGATOR_CALL_WEIGHT if aid in AGGREGATOR_APPS else W_CALL_APP
        for dep in m.requires.get("apps", []):
            if dep in manifests:
                graph[aid][dep] += w
                graph[dep][aid] += w

    # optional_apps edges — soft integrations with graceful fallback. Weaker
    # than hard deps but real: EmptyOS conventions push integrations here, so
    # ignoring them made convention-following apps look like orphans.
    for aid, m in manifests.items():
        w = AGGREGATOR_CALL_WEIGHT if aid in AGGREGATOR_APPS else W_OPTIONAL_APP
        for dep in m.requires.get("optional_apps", []):
            if dep in manifests:
                graph[aid][dep] += w
                graph[dep][aid] += w

    # Contribution edges — [[contributes.<target>.<slot>]] means this app
    # extends the target's surface (design-package sections, cable satellites,
    # dictionary addons). Broad targets are skipped per BROAD_CONTRIB_TARGETS.
    for aid, m in manifests.items():
        for target in m.raw.get("contributes", {}):
            if target == aid or target in BROAD_CONTRIB_TARGETS:
                continue
            if target in manifests:
                graph[aid][target] += W_CONTRIBUTES
                graph[target][aid] += W_CONTRIBUTES

    # Event flow: emitter → listener
    emitters: dict[str, list[str]] = {}  # event_type → [app_ids]
    listeners: dict[str, list[str]] = {}
    for aid, m in manifests.items():
        for ev in m.events_emits:
            emitters.setdefault(ev, []).append(aid)
        for ev in m.events_listens:
            listeners.setdefault(ev, []).append(aid)

    for ev_type, emitter_apps in emitters.items():
        for listener_app in listeners.get(ev_type, []):
            for emitter_app in emitter_apps:
                if emitter_app != listener_app:
                    # Skip event edges TO aggregators — they listen to everything
                    if listener_app in AGGREGATOR_APPS:
                        continue
                    graph[emitter_app][listener_app] += W_EVENT_FLOW
                    graph[listener_app][emitter_app] += W_EVENT_FLOW

    # Shared connectors
    connector_apps: dict[str, list[str]] = defaultdict(list)
    for aid, m in manifests.items():
        for conn in m.requires.get("connectors", []):
            connector_apps[conn].append(aid)
    for conn, apps in connector_apps.items():
        for i, a in enumerate(apps):
            for b in apps[i + 1 :]:
                graph[a][b] += W_SHARED_CONNECTOR
                graph[b][a] += W_SHARED_CONNECTOR

    # Shared required apps — skip pairs where both are aggregators
    req_app_users: dict[str, list[str]] = defaultdict(list)
    for aid, m in manifests.items():
        for dep in m.requires.get("apps", []):
            req_app_users[dep].append(aid)
    for dep, users in req_app_users.items():
        non_agg = [u for u in users if u not in AGGREGATOR_APPS]
        for i, a in enumerate(non_agg):
            for b in non_agg[i + 1 :]:
                graph[a][b] += W_SHARED_REQ_APP
                graph[b][a] += W_SHARED_REQ_APP

    # Shared rare capabilities (speak, listen, draw — distinguishing)
    rare_cap_apps: dict[str, list[str]] = defaultdict(list)
    for aid, m in manifests.items():
        for cap in _rare_caps(m):
            rare_cap_apps[cap].append(aid)
    for cap, apps in rare_cap_apps.items():
        for i, a in enumerate(apps):
            for b in apps[i + 1 :]:
                graph[a][b] += W_RARE_CAP
                graph[b][a] += W_RARE_CAP

    # Semantic edges — apps that SHOULD be near each other based on domain
    # Weight 4 (strong signal — these are intentional domain groupings)
    W_SEMANTIC = 4
    SEMANTIC_PAIRS = [
        # Wellness
        ("healing", "meditation"),
        ("healing", "divination"),
        ("healing", "quotes"),
        ("meditation", "divination"),
        ("meditation", "quotes"),
        ("nutrition", "healing"),
        # Knowledge
        ("assistant", "rooms"),
        ("assistant", "search"),
        ("rooms", "search"),
        ("rooms", "model-bench"),
        ("search", "model-bench"),
        ("dictionary", "search"),
        # Creative / Music
        ("music-studio", "studio"),
        ("music-studio", "media"),
        ("studio", "media"),
        # System / Infra (one group)
        ("settings", "billing"),
        ("settings", "app-analytics"),
        ("billing", "app-analytics"),
        ("system-log", "app-analytics"),
        ("run", "git"),
        ("git", "tmpl"),
        ("run", "tmpl"),
        ("quick-action", "note"),
        ("note", "tmpl"),
        ("link", "app-analytics"),
        ("link", "note"),
        ("app-analytics", "settings"),
        ("quick-action", "settings"),
        ("app-gen", "app-analytics"),
        # Merge infra fragments into one system cluster
        ("note", "quick-action"),
        ("note", "settings"),
        ("quick-action", "run"),
        ("tmpl", "settings"),
        ("git", "settings"),
        ("run", "settings"),
        ("link", "settings"),
        ("note", "run"),
        ("quick-action", "tmpl"),
        # Life — daily life management apps
        ("items", "places"),
        ("items", "contacts"),
        ("places", "contacts"),
        ("items", "expense"),
        ("expense", "journal"),
        ("expense", "contacts"),
        ("journal", "contacts"),
        ("tracker", "expense"),
        ("tracker", "contacts"),
        ("nutrition", "journal"),
        ("focus", "task"),
        # Voice & English
        ("english", "speaking"),
        ("english", "shadowing"),
        ("english", "voice-review"),
        ("speaking", "shadowing"),
        ("speaking", "voice-review"),
        ("shadowing", "voice-review"),
        ("english", "tts"),
        ("speaking", "tts"),
        ("shadowing", "tts"),
        # Career
        ("jobs", "reader"),
        ("jobs", "briefing"),
        # Knowledge
        ("dictionary", "reader"),
        ("dictionary", "lessons"),
        ("rooms", "dictionary"),
        ("reader", "media"),
        # Aggregators cluster together (they're all meta-apps)
        ("hub", "briefing"),
        ("assistant", "rooms"),
        ("staff", "reactor"),
        # Vault
        ("projects", "timeline"),
        # Wired orphans (2026-04-12)
        ("reminders", "briefing"),
        ("weather", "briefing"),
        ("weather", "hub"),
        ("recipes", "nutrition"),
        ("bookmarks", "search"),
        ("quickref", "search"),
        ("daily-brief", "briefing"),
        ("github-connector", "projects"),
        ("3d-studio", "studio"),
        ("release", "app-analytics"),
        # Body cluster reinforcement (sleep+workout+habits absorbed into healing)
        ("healing", "nutrition"),
    ]
    for a, b in SEMANTIC_PAIRS:
        if a in manifests and b in manifests:
            graph[a][b] += W_SEMANTIC
            graph[b][a] += W_SEMANTIC

    return dict(graph)


def label_propagation(
    graph: dict[str, dict[str, float]],
    all_nodes: list[str],
    max_iter: int = 50,
) -> dict[str, str]:
    """Weighted label propagation. Returns app_id → cluster_label."""
    labels = {n: n for n in all_nodes}

    random.seed(42)  # reproducible clusters
    for _ in range(max_iter):
        changed = False
        order = list(all_nodes)
        random.shuffle(order)

        for node in order:
            neighbors = graph.get(node, {})
            if not neighbors:
                continue

            # Weighted vote for each label (skip neighbors outside our node set)
            votes: dict[str, float] = defaultdict(float)
            for neighbor, weight in neighbors.items():
                if neighbor in labels:
                    votes[labels[neighbor]] += weight

            if not votes:
                continue

            best_label = max(votes, key=votes.get)
            if labels[node] != best_label:
                labels[node] = best_label
                changed = True

        if not changed:
            break

    return labels


# Edge weights to keep when prying apart an oversized community. Tried in
# order: drop the weak promiscuous ties first (shared rare caps = 2, shared
# req-app = 2, shared caps = 0.5), then the soft 3-weight ties, then keep only
# hard call_app / event edges. Each step that yields >1 sub-community wins.
_SPLIT_THRESHOLDS = (3, 4, 5)


def _split_oversized(
    members: list[str],
    graph: dict[str, dict[str, float]],
    depth: int = 0,
) -> list[list[str]]:
    """Break an oversized community into sub-communities using strong edges.

    Label propagation collapses a dense subgraph to a single label, so it
    can't split the "Creative" mega-blob on its own. Here we re-run it on
    progressively-stronger induced subgraphs (weak promiscuous edges dropped
    first); the first threshold that produces >1 group wins, and each piece
    still over ``MAX_CLUSTER_SIZE`` recurses (naturally escalating the
    threshold, since an already-maximally-split community yields one group at
    the lower threshold and falls through to the next). Bounded by the fixed
    threshold ladder + a depth cap — a knot with only strong internal edges is
    returned whole, and the small-cluster merge downstream re-homes any tinies.
    """
    if len(members) <= MAX_CLUSTER_SIZE or depth > 4:
        return [members]
    member_set = set(members)
    for thresh in _SPLIT_THRESHOLDS:
        sub: dict[str, dict[str, float]] = {
            a: {
                b: w
                for b, w in graph.get(a, {}).items()
                if b in member_set and w >= thresh
            }
            for a in members
        }
        labels = label_propagation(sub, members)
        groups: dict[str, list[str]] = defaultdict(list)
        for a in members:
            groups[labels[a]].append(a)
        if len(groups) > 1:
            out: list[list[str]] = []
            for g in groups.values():
                out.extend(_split_oversized(g, graph, depth + 1))
            return out
    return [members]


def _centrality(app_id: str, graph: dict) -> float:
    """Simple weighted degree centrality."""
    return sum(graph.get(app_id, {}).values())


def name_cluster(member_ids: list[str], manifests: dict[str, AppManifest]) -> tuple[str, str]:
    """Auto-name a cluster. Returns (icon, name)."""
    for test_fn, icon, name in CLUSTER_HINTS:
        try:
            if test_fn(member_ids, manifests):
                return icon, name
        except (KeyError, IndexError):
            continue

    # Fallback: name after most connected app
    hub = member_ids[0] if member_ids else "unknown"
    return "📦", manifests[hub].name if hub in manifests else hub.title()


def get_clusters(manifests: dict[str, AppManifest]) -> list[dict[str, Any]]:
    """Full pipeline: build graph → cluster → split/merge → name → sort."""
    if not manifests:
        return []

    all_ids = list(manifests.keys())
    graph = build_graph(manifests)
    labels = label_propagation(graph, all_ids)

    # Group by cluster label
    groups: dict[str, list[str]] = defaultdict(list)
    for app_id, label in labels.items():
        groups[label].append(app_id)

    # Split mega-clusters by extracting known domain groups
    DOMAIN_GROUPS = [
        {"english", "speaking", "shadowing", "voice-review", "tts", "lessons", "podcast"},
        {"healing", "meditation", "divination", "quotes", "nutrition"},
        {"search", "dictionary", "rooms", "model-bench"},
        {"journal", "briefing", "expense", "contacts", "tracker", "items", "places", "daily-brief"},
        {"hub", "reactor", "assistant", "staff"},
    ]

    final_groups: list[list[str]] = []
    for label, members in groups.items():
        if len(members) > MAX_CLUSTER_SIZE:
            remaining = set(members)
            for domain in DOMAIN_GROUPS:
                extracted = [aid for aid in remaining if aid in domain]
                if len(extracted) >= MIN_CLUSTER_SIZE:
                    final_groups.append(extracted)
                    remaining -= set(extracted)
            # Whatever the named domain groups didn't claim is, historically, a
            # single leftover dump (the 78-app "Creative" blob). Label
            # propagation glues dense regions into one label and can't break
            # them, so split the leftover on progressively-stronger induced
            # subgraphs instead of emitting it whole.
            if remaining:
                final_groups.extend(_split_oversized(list(remaining), graph))
        else:
            final_groups.append(members)

    # Merge singletons/tiny clusters into nearest neighbor cluster
    big = [g for g in final_groups if len(g) >= MIN_CLUSTER_SIZE]
    small = [g for g in final_groups if len(g) < MIN_CLUSTER_SIZE]

    for tiny in small:
        best_cluster = None
        best_weight = -1
        # Try graph edges first
        for aid in tiny:
            for i, cluster in enumerate(big):
                w = sum(graph.get(aid, {}).get(cid, 0) for cid in cluster)
                if w > best_weight:
                    best_weight = w
                    best_cluster = i
        if best_cluster is not None and best_weight > 0:
            big[best_cluster].extend(tiny)
        else:
            # No graph edges — use keyword matching
            placed = False
            for aid in tiny:
                target = _keyword_assign(aid, manifests.get(aid))
                if target is not None:
                    for i, cluster in enumerate(big):
                        if any(c in cluster for c in target):
                            big[i].extend(tiny)
                            placed = True
                            break
                if placed:
                    break
            if not placed:
                # Last resort: find or create an appropriate cluster
                sys_idx = next(
                    (i for i, c in enumerate(big) if _is_orphan_cluster(c, manifests)),
                    None,
                )
                if sys_idx is not None:
                    big[sys_idx].extend(tiny)
                else:
                    big.append(tiny)

    # Build cluster objects
    clusters = []
    for members in big:
        members.sort(key=lambda a: _centrality(a, graph), reverse=True)
        icon, name = name_cluster(members, manifests)

        internal_weight = sum(
            graph.get(a, {}).get(b, 0) for a in members for b in members if a != b
        )

        # Low-cohesion catch-all: a sizeable group whose members barely touch
        # each other isn't a domain, it's the pile of standalone/orphan apps
        # the merge step had nowhere better to put. Naming it after its most-
        # connected member ("Portal") is misleading — call it what it is.
        if len(members) >= MAX_CLUSTER_SIZE and internal_weight / len(members) < 1.0:
            icon, name = "📦", "Standalone"

        clusters.append(
            {
                "name": name,
                "icon": icon,
                "apps": [
                    {
                        "id": aid,
                        "name": manifests[aid].name,
                        "description": manifests[aid].description,
                        "web_prefix": manifests[aid].provides.get("web", {}).get("prefix", ""),
                        "cli_commands": manifests[aid].provides.get("cli", {}).get("commands", []),
                        "centrality": round(_centrality(aid, graph), 1),
                    }
                    for aid in members
                ],
                "count": len(members),
                "weight": round(internal_weight, 1),
            }
        )

    clusters.sort(key=lambda c: (c["weight"], c["count"]), reverse=True)
    return clusters


# Keyword-based assignment for orphan apps (no graph edges)
# Maps aid → list of app_ids to cluster near
KEYWORD_GROUPS = {
    # Voice & English
    "dictionary": ["english", "reader"],
    "interview-briefing": ["interview-studio"],
    # Life & Productivity
    "nutrition": ["expense", "focus"],
    "items": ["contacts", "expense"],
    "places": ["contacts", "items"],
    "quick-action": ["note", "task"],
    "note": ["quick-action", "task"],
    # Wellness
    "healing": ["meditation", "divination"],
    "meditation": ["healing", "divination"],
    "quotes": ["healing", "meditation"],
    "divination": ["healing", "meditation"],
    # Creative & Music
    "music-studio": ["studio", "tts", "media"],
    "media": ["music-studio", "studio"],
    "studio": ["tts", "music-studio"],
    # Knowledge
    "rooms": ["assistant", "search"],
    "daily-brief": ["briefing"],
    "weather": ["briefing", "hub"],
    "recipes": ["nutrition"],
    "bookmarks": ["search", "media"],
    "reminders": ["briefing", "task"],
    "3d-studio": ["studio"],
    "release": ["app-analytics", "app-gen"],
    "github-connector": ["projects"],
    "assistant": ["rooms", "search"],
    "search": ["assistant", "rooms"],
    "model-bench": ["assistant", "rooms"],
    # System
    "app-gen": ["reactor", "app-analytics"],
    "app-analytics": ["billing", "system-log"],
    "billing": ["app-analytics", "settings"],
    "system-log": ["app-analytics", "billing"],
    "settings": ["billing", "app-analytics"],
    "run": ["git", "tmpl"],
    "git": ["run", "tmpl"],
    "tmpl": ["run", "git"],
}


def _keyword_assign(aid: str, m: AppManifest | None) -> list[str] | None:
    """Return list of app_ids this orphan should cluster near, or None."""
    if aid in KEYWORD_GROUPS:
        return KEYWORD_GROUPS[aid]
    # Fallback: check description keywords
    if m:
        desc = (m.description + " " + m.name).lower()
        if any(w in desc for w in ("english", "speak", "voice", "pronunciation")):
            return ["english", "speaking"]
        if any(w in desc for w in ("interview", "career", "job")):
            return ["jobs"]
        if any(w in desc for w in ("music", "song", "audio", "lyric")):
            return ["music-studio"]
        if any(w in desc for w in ("mood", "health", "wellness", "dream")):
            return ["healing"]
    return None


def _is_orphan_cluster(members: list[str], manifests: dict) -> bool:
    """Check if a cluster is mostly orphans (no capabilities)."""
    # Unknown members count as capability-less; _caps_set only reads
    # .requires, so a plain empty stand-in avoids a runtime kernel import.
    no_caps = sum(
        1
        for m in members
        if not _caps_set(manifests.get(m) or _EMPTY_MANIFEST)
    )
    return no_caps >= len(members) * 0.5


class _EmptyManifest:
    """Capability-less stand-in for unknown app ids (duck-types .requires)."""

    requires: dict = {}


_EMPTY_MANIFEST = _EmptyManifest()
