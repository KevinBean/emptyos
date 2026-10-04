"""Operator-vs-user trust posture — the one route policy, kernel-side.

A daemon serves one human's *data* (``docs/AUTH.md`` pins that). What this module
adds is the orthogonal fact of whether the human at the browser also *operates
the machine*. In the local, Tailscale-private and self-hosted editions they are
the same person (``operator``). In the public demo and the hosted learner
editions they are not (``user``): the browser user owns their vault but not the
host, the code, the API keys or the process.

**Top-level and stdlib-only on purpose.** The kernel resolves posture at boot and
the web middleware enforces it, and neither may import ``emptyos.sdk`` at module
level (it pulls in ``base_app``) — the same rule as ``nethost.py`` and
``basepath.py``. Keep it dependency-free.

The policy has one table, ``OPERATOR_ROUTES``: the core HTTP routes that expose
the host, the config, the network, the plugins, code install, or generic
dispatch to arbitrary app methods. In ``user`` posture the web layer refuses
them. App ``@web_route`` handlers carry their own ``operator=True`` flag instead
(enforced at ``_add_route`` in ``emptyos/web/server.py``), because they are not
known here — so this table is core routes only. ``check_route_posture.py`` pins
that every core route is classified here or in a reviewed user-route list.

Matching is on the request path with the query string stripped, after the
normalisation the browsers and proxies in front of us may or may not apply:
collapse repeated slashes, drop ``.`` and resolve ``..`` segments, and lower-case
for comparison. A prefix entry ending ``/*`` matches the prefix and anything
below it; an exact entry matches only itself (and a trailing slash). This runs
in front of ``AuthMiddleware`` on every request, so it must be cheap and must
never raise.
"""

from __future__ import annotations

__all__ = ["OPERATOR_ROUTES", "normalise_path", "is_operator_route"]

#: Core routes reachable only in operator posture. Prefix entries end ``/*``.
#: Grouped by what each one exposes. App-owned routes (settings/store/kb) are NOT
#: here — they use the ``operator=True`` flag on ``@web_route``.
#: Matching is path-only (method-blind), so an exact entry here must not collide
#: with a user ``{param}`` route: ``/api/jobs/test`` (operator) would also match a
#: GET of a job whose id is literally "test" — harmless (it only ever OVER-blocks,
#: never lets an operator path through), but keep entries off user param spaces.
OPERATOR_ROUTES: tuple[str, ...] = (
    # Generic dispatch to any app method / CLI command.
    "/api/apps/*/rpc/*",
    "/api/cli",
    "/api/delegated-action",
    # Builds that write to the host disk / return host paths.
    "/api/apps/*/export",
    "/api/export-groups/*",
    # Raw LLM proxy — spends the operator's keys with no app in the loop.
    "/v1/*",
    # Network exposure / process / GPU control.
    "/api/tailnet/*",
    "/api/tailnet",
    "/api/health/gpu/free",
    "/api/jobs/test",
    # Operator inventory / audit surfaces.
    "/api/syslog",
    "/api/plugins",
    "/api/services",          # service registry — can reveal external hosts/ports
    "/api/capabilities/full",
    # Cloud policy the operator owns (consent *policy*, not the per-visitor
    # BYOK consent at /api/cloud/consent, which stays user-OK).
    "/api/cloud/approve",
    "/api/cloud/llm-scan",
    "/api/cloud/policy",
    # Rewrites the vault path map.
    "/api/vault-map/rescan",
    "/api/vault-map",
)


def normalise_path(raw: str) -> str:
    """Canonical path for policy comparison: query stripped, slashes collapsed,
    ``.``/``..`` resolved, lower-cased, no trailing slash (except root).

    Defensive against the encodings a hostile caller reaches for — ``//api``,
    ``/api/x/../..``, a trailing ``/`` — but NOT a substitute for the daemon's
    own path handling. ``%2e``/``%2f`` decoding is the front proxy's job (Caddy /
    the control plane already decode once); we compare what the ASGI layer hands
    us, which is already percent-decoded for the path.
    """
    path = (raw or "").split("?", 1)[0].split("#", 1)[0]
    path = path.lower()
    out: list[str] = []
    for seg in path.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if out:
                out.pop()
            continue
        out.append(seg)
    return "/" + "/".join(out)


def is_operator_route(raw_path: str, routes: tuple[str, ...] = OPERATOR_ROUTES) -> bool:
    """True when ``raw_path`` matches an operator-only route.

    A ``/*`` entry matches the prefix segment and anything below it; every other
    entry matches only that exact path. Segment-wise, so ``/api/apps`` never
    matches an entry for ``/api/apps/*/rpc/*`` and ``/api/pluginsX`` never
    matches ``/api/plugins``.
    """
    parts = normalise_path(raw_path).strip("/").split("/") if raw_path else []
    for entry in routes:
        wild = entry.endswith("/*")
        pattern = entry.strip("/")
        if wild:
            pattern = pattern[:-2]  # drop trailing "/*"
        pat_parts = [p for p in pattern.split("/") if p != ""]
        if _segments_match(parts, pat_parts, wild):
            return True
    return False


def _segments_match(path_parts: list[str], pat_parts: list[str], wild: bool) -> bool:
    """Whole-segment match. ``*`` in a pattern segment matches exactly one path
    segment. ``wild`` allows extra trailing path segments (prefix match); without
    it the lengths must be equal (an exact route, trailing slash already
    stripped by normalisation)."""
    if wild:
        if len(path_parts) < len(pat_parts):
            return False
    elif len(path_parts) != len(pat_parts):
        return False
    for got, want in zip(path_parts, pat_parts):
        if want == "*":
            continue
        if got != want:
            return False
    return True
