"""Trusted registry and data overlay for EmptyOS adaptive app icons.

The SVG sprite, manifest ``icon_id`` declarations, API payloads, and browser
helper all share these ids.  Keeping validation here means a manifest can
never turn an arbitrary string into an SVG fragment reference.
"""

from __future__ import annotations

import re
import json
from copy import deepcopy
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


APP_ICON_IDS: tuple[str, ...] = (
    "hub",
    "settings",
    "store",
    "task",
    "journal",
    "projects",
    "focus",
    "search",
    "assistant",
    "quick-action",
    "kb",
    "cad",
    "voice-assistant",
    "boards",
    "publish",
    "dictionary",
    "code",
    "cable-network",
    "jobs",
    "explore",
    "viz",
    "canvas",
    "worklog",
    "studio",
)

APP_ICON_ID_SET = frozenset(APP_ICON_IDS)
# Underscores are allowed because app ids use them (`agent_fleet`), and an id
# the pattern rejects is silently un-iconable — the app simply never appears
# in the library. `_` is safe in all three sinks this guards: a path segment,
# an XML id (NameStartChar includes it), and a URL fragment (unreserved).
# The first character stays alphanumeric so an id can never read as a flag
# or a dotfile.
_ICON_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
APP_ICON_ROLES: tuple[str, ...] = ("paper", "accent", "accent-2", "ink")
_SAFE_TAGS = {"svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon"}
_SAFE_ATTRS = {
    "xmlns", "viewBox", "data-role", "fill", "stroke", "stroke-width",
    "stroke-linecap", "stroke-linejoin", "fill-rule", "clip-rule", "opacity",
    "d", "x", "y", "x1", "y1", "x2", "y2", "width", "height", "rx", "ry",
    "cx", "cy", "r", "points", "transform",
}


def valid_icon_id(value: str) -> bool:
    """Return whether ``value`` is safe for an SVG fragment and data path.

    A rejected id is not merely unstyled — the app is dropped from the icon
    library entirely, so callers that enumerate apps should surface what they
    rejected rather than filtering in silence.
    """
    return bool(_ICON_ID_RE.fullmatch(str(value or "")))


def system_icon_root(data_dir: Path | str) -> Path:
    """Durable Studio-owned overlay location under the global data directory."""
    return Path(data_dir) / "apps" / "studio" / "system-icons"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def validate_role_svg(source: str) -> dict[str, ET.Element]:
    """Validate one editable four-role icon and return its role groups.

    The editable format is deliberately smaller than general SVG: one 1024
    viewBox and exactly four top-level ``g[data-role]`` layers.  It cannot
    contain text, links, CSS, images, scripts, animation, or external content.
    """
    if not isinstance(source, str) or len(source.encode("utf-8")) > 256_000:
        raise ValueError("icon SVG is missing or too large")
    lowered = source.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise ValueError("DOCTYPE and entities are not allowed")
    try:
        root = ET.fromstring(source)
    except ET.ParseError as exc:
        raise ValueError(f"malformed SVG: {exc}") from exc
    if _local(root.tag) != "svg" or root.attrib.get("viewBox") != "0 0 1024 1024":
        raise ValueError("icon SVG must use viewBox 0 0 1024 1024")
    roles: dict[str, ET.Element] = {}
    children = list(root)
    for child in children:
        if _local(child.tag) != "g":
            raise ValueError("icon SVG may only contain top-level paint-role groups")
        role = child.attrib.get("data-role", "")
        if role not in APP_ICON_ROLES or role in roles:
            raise ValueError("icon SVG must contain each paint role exactly once")
        if not list(child):
            raise ValueError(f"paint role {role} must contain geometry")
        roles[role] = child
    if set(roles) != set(APP_ICON_ROLES) or len(children) != len(APP_ICON_ROLES):
        raise ValueError("icon SVG requires paper, accent, accent-2, and ink layers")
    for node in root.iter():
        tag = _local(node.tag)
        if tag not in _SAFE_TAGS:
            raise ValueError(f"unsafe SVG element: {tag}")
        if (node.text or "").strip() or (node.tail or "").strip():
            raise ValueError("visible or embedded text is not allowed")
        for key, value in node.attrib.items():
            name = _local(key)
            lower = str(value).lower()
            if name not in _SAFE_ATTRS or name.lower().startswith("on"):
                raise ValueError(f"unsafe SVG attribute: {name}")
            if name in {"fill", "stroke"} and str(value) not in {"currentColor", "none"}:
                raise ValueError("paint must use currentColor or none inside named role layers")
            if any(token in lower for token in ("url(", "http:", "https:", "data:", "javascript:")):
                raise ValueError("external SVG content is not allowed")
    primitives = {"path", "rect", "circle", "ellipse", "line", "polyline", "polygon"}
    for role, group in roles.items():
        painted = False

        def inspect_paint(node: ET.Element, inherited_fill: str | None = None, inherited_stroke: str | None = None) -> None:
            nonlocal painted
            fill = node.attrib.get("fill", inherited_fill)
            stroke = node.attrib.get("stroke", inherited_stroke)
            if _local(node.tag) in primitives:
                if fill is None and stroke is None:
                    raise ValueError(f"geometry in {role} must declare or inherit its paint")
                if fill == "currentColor" or stroke == "currentColor":
                    painted = True
            for child in node:
                inspect_paint(child, fill, stroke)

        inspect_paint(group)
        if not painted:
            raise ValueError(f"paint role {role} must visibly use currentColor")
    return roles


def load_system_icon_registry(data_dir: Path | str) -> dict:
    """Load the approved overlay registry, failing closed on bad data."""
    path = system_icon_root(data_dir) / "registry.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {"revision": 0, "icons": {}}
    icons = raw.get("icons") if isinstance(raw, dict) else None
    if not isinstance(icons, dict):
        return {"revision": 0, "icons": {}}
    safe = {}
    root = system_icon_root(data_dir).resolve()
    for app_id, record in icons.items():
        if not valid_icon_id(app_id) or not isinstance(record, dict):
            continue
        revision = str(record.get("active_revision") or "")
        if not re.fullmatch(r"r\d{2,4}", revision):
            continue
        path = (root / "apps" / app_id / f"{revision}.svg").resolve()
        try:
            path.relative_to(root)
            validate_role_svg(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        safe[app_id] = {"active_revision": revision, "updated_at": str(record.get("updated_at") or "")}
    try:
        revision_number = int(raw.get("revision") or 0)
    except (TypeError, ValueError):
        revision_number = 0
    return {"revision": revision_number, "icons": safe}


def active_system_icon_ids(data_dir: Path | str) -> frozenset[str]:
    return frozenset(load_system_icon_registry(data_dir)["icons"])


def effective_sprite(source: str, data_dir: Path | str) -> str:
    """Merge approved data-layer icons into the checked-in base sprite."""
    try:
        root = ET.fromstring(source)
    except ET.ParseError as exc:
        raise ValueError(f"invalid built-in icon sprite: {exc}") from exc
    registry = load_system_icon_registry(data_dir)
    overlay_root = system_icon_root(data_dir)
    replacements = {f"app-{app_id}-{role}" for app_id in registry["icons"] for role in APP_ICON_ROLES}
    for node in list(root):
        if node.attrib.get("id") in replacements:
            root.remove(node)
    for app_id, record in registry["icons"].items():
        svg = (overlay_root / "apps" / app_id / f"{record['active_revision']}.svg").read_text(encoding="utf-8")
        roles = validate_role_svg(svg)
        namespace = root.tag.split("}", 1)[0] + "}" if root.tag.startswith("{") else ""
        for role in APP_ICON_ROLES:
            symbol = ET.Element(f"{namespace}symbol", {"id": f"app-{app_id}-{role}", "viewBox": "0 0 1024 1024"})
            for child in list(roles[role]):
                symbol.append(deepcopy(child))
            root.append(symbol)
    return ET.tostring(root, encoding="unicode")


def manifest_icon_id(manifest: Any, active_ids: set[str] | frozenset[str] = frozenset()) -> str:
    """Return a registered icon id from a manifest, otherwise ``""``."""
    manifest_id = str(getattr(manifest, "id", "") or "")
    if manifest_id in active_ids and valid_icon_id(manifest_id):
        return manifest_id
    raw = getattr(manifest, "raw", None) or {}
    app = raw.get("app", {}) or {}
    value = str(app.get("icon_id") or "").strip()
    if not _ICON_ID_RE.fullmatch(value) or value not in APP_ICON_ID_SET:
        return ""
    return value
