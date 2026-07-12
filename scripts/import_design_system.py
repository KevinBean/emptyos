"""Import an awesome-design-md DESIGN.md into an EmptyOS KB `pattern` note.

awesome-design-md (https://github.com/VoltAgent/awesome-design-md, MIT) publishes
~86 brand design systems as DESIGN.md files: YAML frontmatter tokens (colors,
typography, rounded, spacing, components) + prose. EmptyOS's `designer` app
consumes design systems as KB `kind: pattern` notes (topic: ui-design), and the
few-shot injector (`emptyos.sdk.pattern_examples.resolve_pattern_examples`) only
extracts FENCED css/html code blocks — so a raw DESIGN.md (YAML, no fences) won't
drive generation.

This converter turns the YAML tokens into a fenced ```css block (a :root token
set + a type scale + component rules with {colors.x}/{rounded.x}/… refs resolved
to CSS vars) and writes a pattern note alongside the hand-authored
design-system-linear / design-system-stripe notes. Reusable across all 86 brands.

Usage (no kernel import — pure file I/O + network fetch):
    python scripts/import_design_system.py vercel notion cal mintlify framer
    python scripts/import_design_system.py --date 2026-06-06 vercel
    python scripts/import_design_system.py --from-file /path/to/DESIGN.md --slug acme

Vault root is read from emptyos.toml [notes].path. Notes land at
<vault>/30_Resources/EmptyOS/kb/notes/design-system-<slug>.md.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
import tomllib
import urllib.request
from pathlib import Path

import yaml

# Reuse the DESIGN.md token linter (hyphenated filename → load by path).
import importlib.util as _ilu

_lint_spec = _ilu.spec_from_file_location(
    "check_design_md", Path(__file__).resolve().parent / "check-design-md.py"
)
_lint_mod = _ilu.module_from_spec(_lint_spec)
_lint_spec.loader.exec_module(_lint_mod)
lint_design_md = _lint_mod.lint_design_md

RAW_URL = "https://raw.githubusercontent.com/VoltAgent/awesome-design-md/main/design-md/{folder}/DESIGN.md"

_REF_RE = re.compile(r"\{(\w+)\.([\w.-]+)\}")
_CAT_PREFIX = {"colors": "--", "rounded": "--r-", "spacing": "--s-"}

# The 14 keys the publish app's THEME_VARS expects (apps/public/standard/publish/
# templates.py). Emitted as a ```json `publish_theme` fence so a publish site can
# set theme = "design-system-<slug>" and render markdown in this palette. The
# designer few-shot ignores this fence (it extracts only css/html).
_PUBLISH_THEME_KEYS = (
    "bg", "bg_card", "bg_input", "text", "text_heading", "text_secondary",
    "text_muted", "border", "border_strong", "accent", "accent_bg",
    "success", "warning", "danger",
)


def _first(colors: dict, *names, default=None):
    for n in names:
        v = colors.get(n)
        if v:
            return v
    return default


def _rgba(hexv: str, a: float = 0.08) -> str:
    h = str(hexv or "").lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f"rgba({r},{g},{b},{a})"
    except Exception:
        return f"rgba(108,92,231,{a})"


def build_publish_theme(d: dict) -> dict:
    """Best-effort map from a DESIGN.md `colors:` block to publish's 14 theme keys.

    Source brands name colours differently (primary/canvas/ink/hairline vs
    accent/bg/text/border); this maps the common roles with fallbacks. The
    output is hand-tunable — it's a starting palette, not a spec.
    """
    c = d.get("colors") or {}
    accent = _first(c, "primary", "accent", "brand", default="#6c5ce7")
    bg = _first(c, "canvas", "bg", "background", "surface", default="#ffffff")
    bg_card = _first(c, "surface-1", "canvas-soft", "surface", "surface-2", "bg-card", "card", default=bg)
    ink = _first(c, "ink", "text", "on-surface", "foreground", default="#1a1a20")
    text_secondary = _first(c, "ink-secondary", "ink-muted", "ink-mute", "body", "text-secondary", "secondary", default=ink)
    text_muted = _first(c, "ink-subtle", "ink-tertiary", "ink-mute-2", "mute", "muted", "text-muted", default=text_secondary)
    border = _first(c, "hairline", "border", "line", default="#e3e8ee")
    return {
        "bg": bg,
        "bg_card": bg_card,
        "bg_input": _first(c, "surface-1", "canvas-soft", "bg-input", "surface", default=bg_card),
        "text": ink,
        "text_heading": _first(c, "ink", "text-heading", "heading", default=ink),
        "text_secondary": text_secondary,
        "text_muted": text_muted,
        "border": border,
        "border_strong": _first(c, "hairline-strong", "border-strong", "hairline-tertiary", default=border),
        "accent": accent,
        "accent_bg": _rgba(accent, 0.08),
        "success": _first(c, "semantic-success", "success", "green", default="#27ae76"),
        "warning": _first(c, "warning", "amber", "lemon", "yellow", default="#d4a017"),
        "danger": _first(c, "error", "danger", "ruby", "red", "semantic-error", default="#d44040"),
    }


def slug_of(folder: str) -> str:
    """linear.app -> linear, mistral.ai -> mistral, cal -> cal."""
    return folder.split(".")[0].strip().lower()


def resolve_refs(value) -> str:
    """Resolve {colors.x}/{rounded.x}/{spacing.x} token refs to CSS var() calls.

    Typography refs (used inside component dicts) have no single CSS value, so
    they're dropped at the call site, not here.
    """
    s = str(value)

    def sub(m: re.Match) -> str:
        cat, key = m.group(1), m.group(2)
        prefix = _CAT_PREFIX.get(cat)
        return f"var({prefix}{key})" if prefix else m.group(0)

    return _REF_RE.sub(sub, s)


def build_css(d: dict) -> str:
    colors = d.get("colors") or {}
    typo = d.get("typography") or {}
    rounded = d.get("rounded") or {}
    spacing = d.get("spacing") or {}
    components = d.get("components") or {}

    lines: list[str] = [":root {"]
    if colors:
        lines.append("  /* colours */")
        for k, v in colors.items():
            lines.append(f"  --{k}: {v};")
    if rounded:
        lines.append("  /* radius */")
        lines.append("  " + " ".join(f"--r-{k}: {v};" for k, v in rounded.items()))
    if spacing:
        lines.append("  /* spacing */")
        lines.append("  " + " ".join(f"--s-{k}: {v};" for k, v in spacing.items()))
    lines.append("}")
    lines.append("")

    # Type scale — one class per typography role.
    if typo:
        lines.append("/* type scale */")
        for name, spec in typo.items():
            if not isinstance(spec, dict):
                continue
            parts = []
            if spec.get("fontFamily"):
                fam = str(spec["fontFamily"]).split(",")[0].strip().strip("'\"")
                parts.append(f"font-family: {fam!r};".replace("'", '"'))
            if spec.get("fontSize"):
                parts.append(f"font-size: {spec['fontSize']};")
            if spec.get("fontWeight"):
                parts.append(f"font-weight: {spec['fontWeight']};")
            if spec.get("lineHeight"):
                parts.append(f"line-height: {spec['lineHeight']};")
            if spec.get("letterSpacing"):
                parts.append(f"letter-spacing: {spec['letterSpacing']};")
            if spec.get("fontFeature"):
                parts.append(f'font-feature-settings: "{spec["fontFeature"]}";')
            if parts:
                lines.append(f".{name} {{ " + " ".join(parts) + " }")
        lines.append("")

    # Component rules — bg / color / radius / padding (typography ref dropped).
    if components:
        lines.append("/* components */")
        for name, spec in components.items():
            if not isinstance(spec, dict):
                continue
            decls = []
            if spec.get("backgroundColor"):
                decls.append(f"background: {resolve_refs(spec['backgroundColor'])};")
            if spec.get("textColor"):
                decls.append(f"color: {resolve_refs(spec['textColor'])};")
            if spec.get("rounded"):
                decls.append(f"border-radius: {resolve_refs(spec['rounded'])};")
            if spec.get("padding"):
                decls.append(f"padding: {resolve_refs(spec['padding'])};")
            if spec.get("height"):
                decls.append(f"height: {resolve_refs(spec['height'])};")
            if decls:
                lines.append(f".{name} {{ " + " ".join(decls) + " }")

    return "\n".join(lines)


def extract_section(body: str, heading: str) -> str:
    """Pull one `## heading` section's text out of the markdown body."""
    pat = re.compile(rf"^##\s+{re.escape(heading)}\s*$(.*?)(?=^##\s+|\Z)", re.MULTILINE | re.DOTALL)
    m = pat.search(body)
    return m.group(1).strip() if m else ""


def build_note(folder: str, raw: str, date: str) -> tuple[str, str]:
    parts = raw.split("---", 2)
    if len(parts) < 3 or not parts[0].strip() == "":
        # frontmatter is parts[1]; body is parts[2]
        if raw.lstrip().startswith("---"):
            _, fm_text, body = raw.split("---", 2)
        else:
            raise ValueError("no frontmatter found")
    else:
        fm_text, body = parts[1], parts[2]

    d = yaml.safe_load(fm_text) or {}
    lint_errors = lint_design_md(d)
    if lint_errors:
        raise ValueError(
            "malformed DESIGN.md — " + "; ".join(lint_errors[:5])
            + (f" (+{len(lint_errors) - 5} more)" if len(lint_errors) > 5 else "")
        )
    slug = slug_of(folder)
    name = d.get("name") or slug
    desc = (d.get("description") or "").strip()

    css = build_css(d)
    overview = extract_section(body, "Overview")
    dosdonts = extract_section(body, "Do's and Don'ts") or extract_section(body, "Dos and Don'ts")

    title = f"Pattern: {slug.capitalize()} UI design system"
    fm = (
        "---\n"
        "tags:\n  - kb\n"
        "kind: pattern\n"
        f'title: "{title}"\n'
        f"created: {date}\n"
        f"updated: {date}\n"
        "domain: design\n"
        "topic: ui-design\n"
        f'source: "VoltAgent/awesome-design-md — design-md/{folder}/DESIGN.md (MIT)"\n'
        "related:\n  - design-system-linear\n  - design-system-stripe\n"
        "---\n"
    )

    note = [fm, "", f"# {title}", ""]
    if desc:
        note += [desc, ""]
    note += [
        "Use this as scaffolding when the brief wants this brand's aesthetic. "
        "Match the tokens, type scale, and component shapes; adapt layout to the "
        "brief. Auto-imported from awesome-design-md — tokens are a curated prior, "
        "not the brand's official spec.",
        "",
        "## Tokens + components (CSS)",
        "",
        "```css",
        css,
        "```",
        "",
        "## Publish theme (consumed by the publish app)",
        "",
        "Best-effort token map → publish's 14 theme keys. A publish site can set "
        f"`theme = \"design-system-{slug}\"` to render its markdown in this palette. "
        "Hand-tune if the auto-map is off. (The designer few-shot ignores this fence.)",
        "",
        "```json",
        json.dumps({"publish_theme": build_publish_theme(d)}, indent=2),
        "```",
        "",
    ]
    if overview:
        note += ["## Anatomy", "", overview, ""]
    if dosdonts:
        note += ["## Do's and Don'ts", "", dosdonts, ""]

    return slug, "\n".join(note)


def vault_root() -> Path:
    repo = Path(__file__).resolve().parent.parent
    with open(repo / "emptyos.toml", "rb") as f:
        cfg = tomllib.load(f)
    p = (cfg.get("notes") or {}).get("path") or ""
    if not p:
        sys.exit("emptyos.toml [notes].path not set")
    return Path(p)


def main() -> None:
    ap = argparse.ArgumentParser(description="Import awesome-design-md DESIGN.md → KB pattern note")
    ap.add_argument("folders", nargs="*", help="brand folder name(s), e.g. vercel notion cal")
    ap.add_argument("--from-file", help="read a DESIGN.md from a local path instead of fetching")
    ap.add_argument("--slug", help="override slug when using --from-file")
    ap.add_argument("--date", default=datetime.date.today().isoformat(), help="created/updated date")
    args = ap.parse_args()

    notes_dir = vault_root() / "30_Resources/EmptyOS/kb/notes"
    notes_dir.mkdir(parents=True, exist_ok=True)

    jobs: list[tuple[str, str]] = []  # (folder, raw_text)
    if args.from_file:
        raw = Path(args.from_file).read_text(encoding="utf-8")
        jobs.append((args.slug or Path(args.from_file).parent.name, raw))
    for folder in args.folders:
        url = RAW_URL.format(folder=folder)
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                raw = r.read().decode("utf-8")
        except Exception as e:
            print(f"FAIL {folder}: fetch error {e}")
            continue
        jobs.append((folder, raw))

    for folder, raw in jobs:
        try:
            slug, note = build_note(folder, raw, args.date)
        except Exception as e:
            print(f"FAIL {folder}: parse error {e}")
            continue
        out = notes_dir / f"design-system-{slug}.md"
        out.write_text(note, encoding="utf-8")
        css_lines = note.count("\n", note.find("```css"), note.find("```", note.find("```css") + 6))
        print(f"OK   {folder} -> {out.name} ({css_lines} css lines, {len(note)} bytes)")


if __name__ == "__main__":
    main()
