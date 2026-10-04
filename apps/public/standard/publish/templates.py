"""HTML templates for the published site.

Self-contained — generated pages don't depend on a running EmptyOS instance.
Uses Python string .format() with named placeholders.
"""

import json
import re
from pathlib import Path

from emptyos.sdk import theme_tokens

# --- Theme CSS (embedded, no external dependency) ---

# Derived from emptyos/web/static/theme.css at import — NOT a copy.
#
# This used to be a hand-maintained dict of the same tokens, and it drifted:
# it stayed frozen on the pre-2026-07-11 palettes, so every published site
# shipped the exact values the readability audit had already fixed upstream
# (soft-light `success: #34d399` at 1.9:1 on white, `warning: #d97706` at
# 2.99:1, eos's `#27ae76`/`#d4a017`). It also silently missed every theme
# added after it — digital-garden, then tatami/forest/deep-sea/vino.
#
# Deriving means a theme.css fix reaches published sites on the next build,
# and a new theme is selectable with no edit here.
THEME_VARS = theme_tokens.theme_var_map()

# Light theme vars (for toggle — always included in CSS)
_LIGHT = THEME_VARS["soft-light"]
_DARK_DEFAULT = "void-dark"

# The 14 keys a theme must provide. A design-system KB note (topic: ui-design)
# carries these in a ```json `publish_theme` fence, so a site can set
# `theme = "design-system-<slug>"` and render in that palette — the same token
# set the `designer` app few-shots off. One registry, two consumers.
_THEME_KEYS = (
    "bg", "bg_card", "bg_input", "text", "text_heading", "text_secondary",
    "text_muted", "border", "border_strong", "accent", "accent_bg",
    "success", "warning", "danger",
)
_JSON_FENCE_RE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)


def load_design_system_theme(vault_root, slug: str) -> dict | None:
    """Read a `design-system-<slug>` KB note's `publish_theme` json fence, or None.

    Pure file read (no kernel/kb-app dependency) so it works inside the sync
    builder and even when the kb app is disabled.
    """
    try:
        p = Path(vault_root) / "30_Resources/EmptyOS/kb/notes" / f"{slug}.md"
        if not p.exists():
            return None
        body = p.read_text(encoding="utf-8")
    except Exception:
        return None
    for m in _JSON_FENCE_RE.finditer(body):
        try:
            data = json.loads(m.group(1))
        except Exception:
            continue
        pt = data.get("publish_theme") if isinstance(data, dict) else None
        if isinstance(pt, dict):
            return pt
    return None


def resolve_theme_vars(theme: str, vault_root=None) -> dict:
    """Resolve a theme id to a 14-key var dict: built-in THEME_VARS, else a
    design-system KB note's publish_theme (missing keys filled from `eos` so a
    partial map never breaks a build), else the dark default."""
    base = THEME_VARS.get(theme)
    if base:
        return base
    if vault_root is not None and isinstance(theme, str) and theme.startswith("design-system-"):
        pt = load_design_system_theme(vault_root, theme)
        if pt:
            return {**THEME_VARS["eos"], **{k: pt[k] for k in _THEME_KEYS if pt.get(k)}}
    return THEME_VARS.get(theme, THEME_VARS["void-dark"])


def get_site_css(theme: str = "void-dark", vars: dict | None = None) -> str:
    """Generate self-contained site CSS with the chosen theme + alternate toggle.

    `vars` (a 14-key dict from `resolve_theme_vars`) overrides the THEME_VARS
    lookup — that's how a `design-system-*` theme is injected.
    """
    v = vars or THEME_VARS.get(theme, THEME_VARS["void-dark"])
    # Toggle gives the opposite: if dark theme chosen, toggle to light; if light, toggle to dark
    is_light = theme == "soft-light"
    alt = THEME_VARS["void-dark"] if is_light else _LIGHT
    lt = alt  # 'body.alt' = the alternate theme
    return f"""@import url('https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,100..1000;1,9..40,100..1000&family=IBM+Plex+Mono:wght@400;500;600&display=swap');
:root {{
  --bg: {v["bg"]}; --bg-card: {v["bg_card"]}; --bg-input: {v["bg_input"]};
  --text: {v["text"]}; --text-heading: {v["text_heading"]};
  --text-secondary: {v["text_secondary"]}; --text-muted: {v["text_muted"]};
  --border: {v["border"]}; --border-strong: {v["border_strong"]};
  --accent: {v["accent"]}; --accent-bg: {v["accent_bg"]};
  --success: {v["success"]}; --warning: {v["warning"]}; --danger: {v["danger"]};
  --radius: 8px; --radius-lg: 14px;
  --font: 'DM Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
  --mono: 'IBM Plex Mono', 'JetBrains Mono', monospace;
}}
body.alt-theme {{
  --bg: {lt["bg"]}; --bg-card: {lt["bg_card"]}; --bg-input: {lt["bg_input"]};
  --text: {lt["text"]}; --text-heading: {lt["text_heading"]};
  --text-secondary: {lt["text_secondary"]}; --text-muted: {lt["text_muted"]};
  --border: {lt["border"]}; --border-strong: {lt["border_strong"]};
  --accent: {lt["accent"]}; --accent-bg: {lt["accent_bg"]};
  --success: {lt["success"]}; --warning: {lt["warning"]}; --danger: {lt["danger"]};
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html {{ scroll-behavior: smooth; }}
body {{
  font-family: var(--font); background: var(--bg); color: var(--text);
  line-height: 1.7; -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale;
  text-rendering: optimizeLegibility;
  transition: background-color 0.3s, color 0.3s;
}}
::selection {{ background: color-mix(in srgb, var(--accent) 25%, transparent); color: var(--text-heading); }}
a {{ color: var(--accent); text-decoration: none; transition: color 0.15s; }}
a:hover {{ text-decoration: underline; text-underline-offset: 3px; text-decoration-thickness: 1px; }}
::-webkit-scrollbar {{ width: 5px; }}
::-webkit-scrollbar-track {{ background: transparent; }}
::-webkit-scrollbar-thumb {{ background: var(--border-strong); border-radius: 3px; }}
::-webkit-scrollbar-thumb:hover {{ background: var(--text-muted); }}

/* Layout */
.site-header {{
  border-bottom: 1px solid var(--border); padding: 14px 0; position: sticky; top: 0;
  background: color-mix(in srgb, var(--bg) 90%, transparent); z-index: 10;
  backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
}}
.site-header .container {{
  display: flex; align-items: center; justify-content: space-between;
  max-width: 900px; gap: 32px;
}}
.site-name {{ font-size: 1.05rem; font-weight: 700; color: var(--text-heading); text-decoration: none; letter-spacing: -0.01em; white-space: nowrap; flex-shrink: 0; }}
.site-name:hover {{ text-decoration: none; opacity: 0.8; }}
.site-nav {{ display: flex; gap: 18px; font-size: 0.85rem; align-items: center; flex-wrap: wrap; justify-content: flex-end; }}
.site-nav a {{ color: var(--text-secondary); transition: color 0.15s; white-space: nowrap; }}
.site-nav a:hover {{ color: var(--accent); text-decoration: none; }}
.container {{ max-width: 900px; margin: 0 auto; padding: 0 20px; }}
.container-wide {{ max-width: 900px; margin: 0 auto; padding: 0 20px; }}
.site-footer {{
  border-top: 1px solid var(--border); padding: 28px 0;
  margin-top: 56px; color: var(--text-muted); font-size: 0.8rem; text-align: center;
  letter-spacing: 0.01em;
}}
.site-footer a {{ color: var(--text-muted); }}
.site-footer a:hover {{ color: var(--accent); text-decoration: none; }}
.cross-site-links {{ display: block; margin-bottom: 8px; }}

/* Theme toggle */
.theme-toggle {{
  background: none; border: 1px solid var(--border); border-radius: 6px;
  color: var(--text-muted); cursor: pointer; font-size: 16px; padding: 4px 8px;
  transition: color 0.2s, border-color 0.2s; line-height: 1;
}}
.theme-toggle:hover {{ color: var(--accent); border-color: var(--accent); }}

/* Search */
.search-toggle {{ background: none; border: none; color: var(--text-muted); cursor: pointer; font-size: 16px; padding: 4px; }}
.search-toggle:hover {{ color: var(--accent); }}
.search-overlay {{
  display: none; position: fixed; inset: 0; background: color-mix(in srgb, var(--text) 30%, transparent);
  z-index: 100; backdrop-filter: blur(6px); -webkit-backdrop-filter: blur(6px);
}}
.search-overlay.open {{ display: flex; align-items: flex-start; justify-content: center; padding-top: 15vh; }}
.search-box {{
  background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius-lg);
  width: min(560px, 90vw); padding: 20px; box-shadow: 0 8px 32px rgba(0,0,0,0.3);
}}
.search-input {{
  width: 100%; padding: 12px 16px; background: var(--bg-input); border: 1px solid var(--border);
  border-radius: var(--radius); font-size: 16px; color: var(--text); font-family: var(--font); outline: none;
}}
.search-input:focus {{ border-color: var(--accent); }}
.search-results {{ margin-top: 12px; max-height: 50vh; overflow-y: auto; }}
.search-result {{
  display: block; padding: 10px 12px; border-radius: var(--radius); color: var(--text);
  text-decoration: none; transition: background 0.15s;
}}
.search-result:hover {{ background: var(--bg-card); text-decoration: none; }}
.search-result-title {{ font-weight: 600; color: var(--text-heading); font-size: 0.95rem; }}
.search-result-summary {{ font-size: 0.8rem; color: var(--text-muted); margin-top: 2px; }}

/* Hero */
.hero {{ padding: 56px 0 36px; text-align: center; }}
.hero h1 {{ font-size: 2rem; font-weight: 700; color: var(--text-heading); margin-bottom: 8px; letter-spacing: -0.02em; }}
.hero .tagline {{ font-size: 1rem; color: var(--text-secondary); margin-bottom: 16px; max-width: 480px; margin-left: auto; margin-right: auto; }}
.hero .social {{ display: flex; gap: 16px; justify-content: center; font-size: 0.85rem; }}
.hero .social a {{ color: var(--text-muted); }}
.hero .social a:hover {{ color: var(--accent); text-decoration: none; }}

/* Featured post */
.featured {{
  background: var(--bg-card); border: 1px solid var(--border); border-left: 3px solid var(--accent);
  border-radius: var(--radius-lg); padding: 24px; margin-bottom: 32px;
  display: flex; gap: 24px; align-items: flex-start;
  transition: box-shadow 0.25s, transform 0.2s;
}}
.featured:hover {{ box-shadow: 0 6px 24px color-mix(in srgb, var(--accent) 8%, transparent); transform: translateY(-1px); }}
.featured .featured-text {{ flex: 1; min-width: 0; }}
.featured .label {{ font-size: 0.7rem; text-transform: uppercase; letter-spacing: 1.5px; color: var(--accent); font-weight: 600; margin-bottom: 8px; }}
.featured .title {{ font-size: 1.4rem; font-weight: 700; color: var(--text-heading); margin-bottom: 6px; letter-spacing: -0.01em; }}
.featured .summary {{ font-size: 0.9rem; color: var(--text-secondary); margin-bottom: 10px; line-height: 1.6; }}
.featured .meta {{ font-size: 0.8rem; color: var(--text-muted); }}
.featured .featured-cover {{ flex: 0 0 auto; width: 180px; height: auto; max-height: 180px; border-radius: 10px; margin: 0; }}
.post-card .card-cover {{ display: block; max-width: 100%; max-height: 140px; width: auto; height: auto; margin: 0 auto 10px; border-radius: 8px; }}

/* Post grid */
.post-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 16px; margin-bottom: 32px; }}
.post-card {{
  background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius-lg);
  padding: 20px; transition: border-color 0.2s, box-shadow 0.25s, transform 0.2s;
}}
.post-card:hover {{ border-color: color-mix(in srgb, var(--accent) 40%, var(--border)); box-shadow: 0 4px 16px color-mix(in srgb, var(--accent) 6%, transparent); transform: translateY(-2px); }}
.post-card .card-title {{ font-size: 1rem; font-weight: 600; color: var(--text-heading); margin-bottom: 6px; letter-spacing: -0.01em; }}
.post-card .card-meta {{ font-size: 0.75rem; color: var(--text-muted); margin-bottom: 8px; letter-spacing: 0.02em; }}
.post-card .card-summary {{ font-size: 0.85rem; color: var(--text-secondary); display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; line-height: 1.55; }}
.post-card .card-tags {{ margin-top: 10px; }}

/* Post list (tag pages, related) */
.post-list {{ list-style: none; }}
.post-item {{ padding: 16px 0; border-bottom: 1px solid var(--border); transition: background 0.15s; }}
.post-item:last-child {{ border-bottom: none; }}
.post-title {{ font-size: 1.1rem; font-weight: 600; color: var(--text-heading); letter-spacing: -0.01em; }}
.post-title:hover {{ color: var(--accent); text-decoration: none; }}
.post-meta {{ font-size: 0.78rem; color: var(--text-muted); margin-top: 4px; letter-spacing: 0.02em; }}
.post-summary {{ font-size: 0.9rem; color: var(--text-secondary); margin-top: 6px; line-height: 1.55; }}

/* Tags */
.tag {{ display: inline-block; padding: 3px 10px; border-radius: 12px; font-size: 0.73rem;
  background: var(--accent-bg); color: var(--accent); margin-right: 4px; text-decoration: none;
  font-weight: 500; letter-spacing: 0.02em; transition: background 0.15s; }}
.tag:hover {{ text-decoration: none; background: color-mix(in srgb, var(--accent) 15%, transparent); }}

/* Article content */
.article {{ line-height: 1.85; font-size: 1.02rem; }}
.article h1 {{ font-size: 1.8rem; color: var(--text-heading); margin: 40px 0 14px; font-weight: 700; letter-spacing: -0.02em; }}
.article h2 {{ font-size: 1.4rem; color: var(--text-heading); margin: 36px 0 12px; font-weight: 600; letter-spacing: -0.01em; padding-bottom: 6px; border-bottom: 1px solid var(--border); }}
.article h3 {{ font-size: 1.15rem; color: var(--text-heading); margin: 28px 0 8px; font-weight: 600; }}
.article p {{ margin: 14px 0; }}
.article ul, .article ol {{ margin: 14px 0; padding-left: 24px; }}
.article li {{ margin: 5px 0; }}
.article li::marker {{ color: var(--text-muted); }}
.article blockquote {{
  border-left: 3px solid var(--accent); padding: 12px 20px; margin: 20px 0;
  color: var(--text-secondary); background: var(--accent-bg); border-radius: 0 var(--radius) var(--radius) 0;
  font-style: italic;
}}
.article blockquote p {{ margin: 6px 0; }}
.article img {{ max-width: 100%; border-radius: var(--radius); margin: 20px 0; box-shadow: 0 2px 12px color-mix(in srgb, var(--text) 8%, transparent); }}
.article code {{
  font-family: var(--mono); font-size: 0.84em;
  background: var(--accent-bg); color: var(--text-heading); padding: 2px 6px; border-radius: 4px;
}}
.article pre {{
  background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 18px; overflow-x: auto; margin: 20px 0; line-height: 1.6;
}}
.article pre code {{ background: none; padding: 0; font-size: 0.84rem; color: var(--text); }}
/* Syntax highlighting (pygments codehilite → .highlight) — theme-var based */
.highlight {{ background: none; }}
.highlight .c, .highlight .c1, .highlight .cm, .highlight .cs {{ color: var(--text-muted); font-style: italic; }}
.highlight .k, .highlight .kd, .highlight .kn, .highlight .kc, .highlight .kt, .highlight .kr {{ color: var(--accent); font-weight: 600; }}
.highlight .s, .highlight .s1, .highlight .s2, .highlight .sb, .highlight .sd, .highlight .se, .highlight .sr {{ color: var(--text-heading); }}
.highlight .m, .highlight .mi, .highlight .mf, .highlight .mh, .highlight .mo, .highlight .il {{ color: var(--text-heading); }}
.highlight .nf, .highlight .fm {{ color: var(--accent); }}
.highlight .nc, .highlight .nn, .highlight .ne {{ color: var(--text-heading); font-weight: 600; }}
.highlight .nb, .highlight .bp {{ color: var(--accent); }}
.highlight .nd, .highlight .nl, .highlight .nv, .highlight .vc, .highlight .vg, .highlight .vi {{ color: var(--text); }}
.highlight .o, .highlight .ow, .highlight .p {{ color: var(--text-secondary); }}
.highlight .err {{ color: var(--text); }}
/* Backlinks / linked mentions */
.backlinks {{ margin-top: 40px; padding-top: 20px; border-top: 1px solid var(--border); }}
.backlinks-title {{ font-size: 1rem; color: var(--text-heading); margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }}
.backlinks-count {{ font-size: 0.72rem; font-weight: 700; color: var(--accent); background: var(--accent-bg); border-radius: 999px; padding: 1px 8px; }}
.backlinks-list {{ list-style: none; margin: 0; padding: 0; }}
.backlink-item {{ padding: 8px 0; border-bottom: 1px solid var(--border); }}
.backlink-item:last-child {{ border-bottom: none; }}
.backlink-link {{ font-size: 0.95rem; color: var(--accent); text-decoration: none; }}
.backlink-link:hover {{ text-decoration: underline; }}
.article table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
.article th {{ text-align: left; font-size: 0.78rem; color: var(--text-muted); text-transform: uppercase;
  letter-spacing: 0.6px; padding: 10px 8px; border-bottom: 2px solid var(--accent); font-weight: 600; }}
.article td {{ padding: 10px 8px; border-bottom: 1px solid var(--border); font-size: 0.9rem; }}
.article tr:hover td {{ background: var(--accent-bg); }}
.article hr {{ border: none; border-top: 1px solid var(--border); margin: 32px 0; }}
.article strong {{ color: var(--text-heading); }}

/* Callouts */
.callout {{
  border-left: 3px solid var(--accent); padding: 12px 16px; margin: 16px 0;
  background: var(--bg-card); border-radius: 0 var(--radius) var(--radius) 0;
}}
.callout-title {{ font-weight: 600; color: var(--text-heading); }}
.callout-note {{ border-left-color: var(--accent); }}
.callout-warning {{ border-left-color: var(--warning); }}
.callout-tip {{ border-left-color: var(--success); }}
.callout-danger {{ border-left-color: var(--danger); }}

/* Wikilinks */
.wikilink {{ color: var(--accent); text-decoration: none; border-bottom: 1px dashed color-mix(in srgb, var(--accent) 40%, transparent); transition: border-color 0.15s; }}
.wikilink:hover {{ border-bottom-style: solid; border-bottom-color: var(--accent); text-decoration: none; }}
.wikilink-private {{ color: var(--text-muted); border-bottom-color: var(--border); }}

/* Header permalink */
.header-link {{ color: var(--text-muted); margin-left: 8px; font-size: 0.8em; opacity: 0; transition: opacity 0.2s; text-decoration: none; }}
.header-link:hover {{ color: var(--accent); text-decoration: none; }}
h1:hover .header-link, h2:hover .header-link, h3:hover .header-link {{ opacity: 0.6; }}
h1:hover .header-link:hover, h2:hover .header-link:hover, h3:hover .header-link:hover {{ opacity: 1; }}

/* Video embeds */
.video-embed {{ position: relative; padding-bottom: 56.25%; height: 0; overflow: hidden; margin: 16px 0; border-radius: var(--radius); }}
.video-embed iframe {{ position: absolute; top: 0; left: 0; width: 100%; height: 100%; border: none; border-radius: var(--radius); }}

/* Audio */
audio {{ width: 100%; margin: 12px 0; border-radius: var(--radius); }}

/* Avatar */
.hero-avatar {{ width: 80px; height: 80px; border-radius: 50%; margin-bottom: 12px; border: 2px solid var(--border); }}
.author-avatar {{ width: 56px; height: 56px; border-radius: 50%; border: 2px solid var(--border); flex-shrink: 0; }}

/* Hero-pro (EE-prominent, opt-in via site config hero_role) */
.hero-pro {{ padding: 32px 0 20px; text-align: center; }}
.hero-pro .hero-avatar {{ margin: 0 auto 10px; display: block; width: 56px; height: 56px; }}
.hero-pro .name {{ font-size: 2rem; font-weight: 700; color: var(--text-heading); margin-bottom: 4px; letter-spacing: -0.025em; line-height: 1.15; }}
.hero-pro .role {{ font-size: 0.95rem; font-weight: 500; color: var(--text-heading); margin-bottom: 4px; letter-spacing: -0.005em; max-width: 760px; margin-left: auto; margin-right: auto; line-height: 1.4; }}
.hero-pro .summary {{ font-size: 0.88rem; color: var(--text-secondary); margin-bottom: 18px; max-width: 680px; margin-left: auto; margin-right: auto; line-height: 1.55; }}
.hero-pro .stats-group {{ margin: 0 auto 10px; max-width: 900px; }}
.hero-pro .stats-group:last-of-type {{ margin-bottom: 18px; }}
.hero-pro .stats-label {{
  font-size: 0.64rem; text-transform: uppercase; letter-spacing: 1.8px;
  color: var(--text-muted); font-weight: 600; margin-bottom: 5px; text-align: center;
}}
.hero-pro .stats {{
  display: grid; grid-auto-flow: column; grid-auto-columns: 1fr;
  border: 1px solid var(--border); border-radius: var(--radius-lg);
  background: var(--bg-card); overflow: hidden;
}}
.hero-pro .stats .stat {{
  padding: 12px 12px; text-align: center;
  border-right: 1px solid var(--border);
  font-size: 0.78rem; color: var(--text-secondary); letter-spacing: 0.01em;
  min-width: 0;
  display: flex; flex-direction: column; justify-content: center;
}}
.hero-pro .stats .stat:last-child {{ border-right: none; }}
.hero-pro .stats .stat strong {{ display: block; color: var(--text-heading); font-size: 0.92rem; font-weight: 700; margin-bottom: 4px; letter-spacing: -0.01em; line-height: 1.25; }}
.hero-pro .cta {{ display: flex; gap: 12px; justify-content: center; flex-wrap: wrap; margin-bottom: 16px; }}
.hero-pro .cta a {{
  display: inline-flex; align-items: center; gap: 6px;
  padding: 9px 18px; border-radius: var(--radius);
  font-size: 0.9rem; font-weight: 600; letter-spacing: 0.005em;
  text-decoration: none; transition: transform 0.15s, box-shadow 0.2s, background 0.2s;
}}
.hero-pro .cta a.primary {{ background: var(--accent); color: #fff; border: 1px solid var(--accent); }}
.hero-pro .cta a.primary:hover {{ transform: translateY(-1px); box-shadow: 0 4px 14px color-mix(in srgb, var(--accent) 30%, transparent); text-decoration: none; }}
.hero-pro .cta a.secondary {{ background: transparent; color: var(--text-heading); border: 1px solid var(--border-strong); }}
.hero-pro .cta a.secondary:hover {{ border-color: var(--accent); color: var(--accent); text-decoration: none; }}
.hero-pro .secondary-line {{
  max-width: 640px; margin: 0 auto 14px; padding: 0;
  font-size: 0.85rem; color: var(--text-secondary); line-height: 1.55;
}}
.hero-pro .secondary-line strong {{ color: var(--text-heading); font-weight: 600; }}
.hero-pro .social {{ display: flex; gap: 16px; justify-content: center; font-size: 0.82rem; }}
.hero-pro .social a {{ color: var(--text-muted); }}
.hero-pro .social a:hover {{ color: var(--accent); text-decoration: none; }}
.hero-pro .social-inline {{
  font-size: 0.82rem; gap: 16px; margin-bottom: 10px;
}}
.hero-pro .social-inline a {{
  color: var(--accent); font-weight: 500; letter-spacing: 0.01em;
}}
.hero-pro .social-inline a:hover {{ text-decoration: underline; text-underline-offset: 3px; }}
@media (max-width: 640px) {{
  .hero-pro {{ padding: 36px 0 20px; }}
  .hero-pro .name {{ font-size: 1.7rem; }}
  .hero-pro .role {{ font-size: 0.95rem; }}
  .hero-pro .summary {{ font-size: 0.9rem; }}
  .hero-pro .stats {{ grid-auto-flow: row; grid-auto-columns: auto; }}
  .hero-pro .stats .stat {{ border-right: none; border-bottom: 1px solid var(--border); }}
  .hero-pro .stats .stat:last-child {{ border-bottom: none; }}
  .hero-pro .cta a {{ width: 100%; justify-content: center; }}
}}

/* Author card */
.author-card {{
  display: flex; align-items: center; gap: 16px; padding: 22px;
  background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius-lg);
  margin-top: 44px; border-left: 3px solid var(--accent);
}}
.author-card .author-info {{ flex: 1; }}
.author-card .author-name {{ font-weight: 700; color: var(--text-heading); font-size: 1rem; }}
.author-card .author-bio {{ font-size: 0.85rem; color: var(--text-secondary); margin-top: 4px; }}
.author-card .author-links {{ font-size: 0.8rem; margin-top: 6px; }}
.author-card .author-links a {{ color: var(--text-muted); margin-right: 12px; }}
.author-card .author-links a:hover {{ color: var(--accent); text-decoration: none; }}

/* Language switcher */
.lang-switch {{
  display: flex; gap: 6px; margin-bottom: 16px;
}}
.lang-switch a {{
  padding: 3px 10px; border-radius: 4px; font-size: 0.75rem; font-weight: 500;
  color: var(--text-muted); border: 1px solid var(--border); text-decoration: none;
}}
.lang-switch a:hover {{ border-color: var(--accent); color: var(--accent); text-decoration: none; }}
.lang-switch a.active {{ background: var(--accent); color: #fff; border-color: var(--accent); }}

/* AI translation notice */
.ai-notice {{
  padding: 10px 14px; margin-bottom: 20px; border-radius: var(--radius);
  background: var(--accent-bg); border: 1px solid color-mix(in srgb, var(--accent) 20%, transparent);
  font-size: 0.8rem; color: var(--text-secondary);
}}

/* TOC sidebar */
.post-layout {{ display: grid; grid-template-columns: 1fr 200px; gap: 32px; align-items: start; }}
.toc-sidebar {{ position: sticky; top: 80px; font-size: 0.8rem; border-left: 1px solid var(--border); padding-left: 16px; }}
.toc-sidebar .toc-title {{ font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 1px; font-size: 0.68rem; margin-bottom: 10px; }}
.toc-sidebar ul {{ list-style: none; padding: 0; }}
.toc-sidebar li {{ margin: 3px 0; }}
.toc-sidebar a {{ color: var(--text-muted); text-decoration: none; display: block; padding: 3px 0; transition: color 0.15s; font-size: 0.8rem; line-height: 1.5; }}
.toc-sidebar a:hover {{ color: var(--accent); text-decoration: none; }}
.toc-sidebar li li {{ padding-left: 14px; }}
.toc-sidebar li li li {{ padding-left: 14px; }}

/* Section heading */
.section-heading {{ font-size: 0.8rem; text-transform: uppercase; letter-spacing: 1.5px; color: var(--text-muted); font-weight: 600; margin-bottom: 16px; padding-left: 12px; border-left: 3px solid var(--accent); }}

/* Landing page hero */
.landing-hero {{ padding: 72px 0 48px; text-align: center; }}
.landing-hero h1 {{ font-size: 2.6rem; font-weight: 700; color: var(--text-heading); margin-bottom: 14px; letter-spacing: -0.03em; line-height: 1.15; }}
.landing-hero .tagline {{ font-size: 1.1rem; color: var(--text-secondary); margin-bottom: 32px; max-width: 560px; margin-left: auto; margin-right: auto; line-height: 1.7; }}
.landing-cta {{ display: flex; gap: 12px; justify-content: center; flex-wrap: wrap; }}
.hero-note {{ max-width: 560px; margin: 28px auto 0; }}
.hero-note img {{ display: block; width: 100%; max-width: 100%; height: auto; margin: 20px auto 4px; border-radius: var(--radius); border: 1px solid var(--border); box-shadow: 0 8px 28px color-mix(in srgb, var(--text) 9%, transparent); }}
.hero-note blockquote {{
  margin: 0; padding: 14px 18px; border-left: 3px solid var(--accent);
  background: var(--bg-elev); border-radius: var(--radius);
  color: var(--text-secondary); font-size: 0.95rem; line-height: 1.6; text-align: left;
}}
.hero-note blockquote p {{ margin: 0; }}
.hero-note blockquote p + p {{ margin-top: 8px; }}
.gallery-section {{ margin: 64px auto 0; max-width: 1100px; }}
.gallery-section .section-heading {{ text-align: center; font-size: 1.4rem; font-weight: 600; color: var(--text-heading); margin-bottom: 24px; }}
.gallery-grid {{ display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }}
.gallery-tile {{ margin: 0; border-radius: var(--radius); overflow: hidden; background: var(--bg-elev); border: 1px solid var(--border); transition: transform 0.18s, box-shadow 0.2s; }}
.gallery-tile:hover {{ transform: translateY(-2px); box-shadow: 0 6px 24px color-mix(in srgb, var(--text) 12%, transparent); }}
.gallery-tile a {{ display: block; line-height: 0; }}
.gallery-tile img {{ width: 100%; height: auto; display: block; }}
.gallery-tile figcaption {{ padding: 10px 14px; font-size: 0.88rem; color: var(--text-secondary); text-align: center; line-height: 1.4; }}
.metric-strip {{ display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); max-width: 880px; margin: 40px auto 0; }}
.metric-tile {{ padding: 18px 16px; border-radius: var(--radius); background: var(--bg-elev); border: 1px solid var(--border); text-align: center; }}
.metric-value {{ font-size: 1.8rem; font-weight: 700; color: var(--text-heading); line-height: 1.1; letter-spacing: -0.02em; }}
.metric-label {{ font-size: 0.82rem; color: var(--text-secondary); margin-top: 6px; text-transform: uppercase; letter-spacing: 0.06em; }}
.feature-card .feature-icon {{ font-size: 1.8rem; line-height: 1; margin-bottom: 12px; }}
.landing-cta a {{
  display: inline-block; padding: 12px 28px; border-radius: var(--radius);
  font-weight: 600; font-size: 0.95rem; text-decoration: none;
  transition: transform 0.15s, box-shadow 0.2s, opacity 0.2s;
}}
.landing-cta a:hover {{ text-decoration: none; transform: translateY(-1px); }}
.cta-primary {{ background: var(--accent); color: #fff; box-shadow: 0 2px 10px color-mix(in srgb, var(--accent) 20%, transparent); }}
.cta-primary:hover {{ box-shadow: 0 4px 18px color-mix(in srgb, var(--accent) 30%, transparent); }}
.cta-secondary {{ border: 1px solid var(--border-strong); color: var(--text-heading); }}
.cta-secondary:hover {{ border-color: var(--accent); color: var(--accent); }}
.landing-cta a.cta-disabled {{ opacity: 0.55; cursor: not-allowed; pointer-events: none; box-shadow: none; transform: none; }}
.landing-cta a.cta-primary.cta-disabled {{ background: var(--bg-elev); color: var(--text-muted); border: 1px solid var(--border); }}
.landing-cta a.cta-secondary.cta-disabled {{ color: var(--text-muted); border-color: var(--border); }}

/* Feature grid */
.feature-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 20px; margin: 40px 0; }}
.feature-card {{
  background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius-lg);
  padding: 24px; transition: border-color 0.2s, box-shadow 0.25s, transform 0.2s;
}}
.feature-card:hover {{ border-color: color-mix(in srgb, var(--accent) 40%, transparent); box-shadow: 0 4px 16px color-mix(in srgb, var(--accent) 6%, transparent); transform: translateY(-1px); }}
.feature-card h3 {{ font-size: 1.05rem; font-weight: 600; color: var(--text-heading); margin-bottom: 8px; }}
.feature-card .fc-body {{ font-size: 0.9rem; color: var(--text-secondary); line-height: 1.6; }}
.feature-card .fc-body p {{ margin: 4px 0; }}

/* Editions concept band — one core → many uses */
.editions-band {{ margin: 72px auto 0; max-width: 1100px; text-align: center; }}
.editions-core {{ background: linear-gradient(180deg, color-mix(in srgb, var(--accent) 10%, var(--bg-elev)), var(--bg-elev)); border: 1px solid var(--border-strong); border-radius: var(--radius-lg); padding: 32px 28px; max-width: 720px; margin: 0 auto; }}
.editions-core-eyebrow {{ font-size: 0.75rem; text-transform: uppercase; letter-spacing: 2px; color: var(--accent); font-weight: 700; }}
.editions-core-title {{ font-size: 1.5rem; font-weight: 700; color: var(--text-heading); margin-top: 8px; letter-spacing: -0.02em; }}
.editions-core-sub {{ font-size: 0.95rem; color: var(--text-secondary); margin-top: 10px; line-height: 1.6; max-width: 560px; margin-left: auto; margin-right: auto; }}
.editions-fan {{ width: 2px; height: 28px; margin: 4px auto 8px; background: linear-gradient(var(--border-strong), transparent); }}
.editions-group {{ margin-top: 20px; }}
.editions-group-label {{ display: flex; align-items: center; gap: 14px; max-width: 460px; margin: 0 auto 16px; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 1.2px; color: var(--text-muted); font-weight: 600; }}
.editions-group-label::before, .editions-group-label::after {{ content: ""; flex: 1; height: 1px; background: var(--border); }}
.editions-grid {{ display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); }}
.editions-grid-public {{ max-width: 620px; margin: 0 auto; }}
.edition-tile {{ background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius-lg); padding: 20px 18px 16px; text-align: left; transition: border-color 0.2s; }}
.edition-tile:hover {{ border-color: color-mix(in srgb, var(--accent) 30%, var(--border)); }}
.edition-public {{ border-color: color-mix(in srgb, var(--accent) 28%, var(--border)); background: color-mix(in srgb, var(--accent) 5%, var(--bg-card)); }}
.edition-private {{ border-style: dashed; }}
.edition-icon {{ font-size: 1.6rem; line-height: 1; margin-bottom: 10px; }}
.edition-name {{ font-size: 1rem; font-weight: 600; color: var(--text-heading); }}
.edition-oneliner {{ font-size: 0.85rem; color: var(--text-secondary); line-height: 1.5; margin-top: 4px; }}
.edition-badge {{ display: inline-block; margin-top: 12px; font-size: 0.68rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; padding: 3px 8px; border-radius: 999px; }}
.edition-public .edition-badge {{ background: color-mix(in srgb, var(--accent) 16%, transparent); color: var(--accent); }}
.edition-private .edition-badge {{ background: var(--bg-elev); color: var(--text-muted); border: 1px solid var(--border); }}
.editions-foot {{ margin-top: 28px; }}
.editions-foot a {{ color: var(--accent); font-weight: 600; font-size: 0.9rem; text-decoration: none; }}
.editions-foot a:hover {{ text-decoration: underline; }}
@media (max-width: 640px) {{ .editions-core {{ padding: 24px 18px; }} }}

/* Contact CTA band on landing */
.contact-band {{ margin: 72px auto 0; max-width: 720px; text-align: center; background: linear-gradient(180deg, color-mix(in srgb, var(--accent) 8%, var(--bg-elev)), var(--bg-elev)); border: 1px solid var(--border-strong); border-radius: var(--radius-lg); padding: 40px 28px; }}
.contact-heading {{ font-size: 1.3rem; font-weight: 700; color: var(--text-heading); margin-bottom: 20px; letter-spacing: -0.01em; }}
.contact-band .landing-cta {{ margin-top: 0; }}

/* Blog preview on landing */
.blog-preview {{ margin-top: 48px; padding-top: 32px; border-top: 1px solid var(--border); }}
.blog-viewall {{ text-align: center; margin-top: 24px; }}
.blog-viewall a {{ color: var(--accent); font-weight: 600; font-size: 0.9rem; text-decoration: none; }}
.blog-viewall a:hover {{ text-decoration: underline; }}

/* Docs sidebar layout */
.docs-layout {{ display: grid; grid-template-columns: 200px 1fr; gap: 32px; align-items: start; }}
.docs-sidebar {{
  position: sticky; top: 80px; font-size: 0.85rem; padding-top: 8px;
}}
.docs-sidebar .sidebar-title {{
  font-weight: 600; color: var(--text-muted); text-transform: uppercase;
  letter-spacing: 0.5px; font-size: 0.7rem; margin-bottom: 12px;
}}
.docs-sidebar ul {{ list-style: none; padding: 0; }}
.docs-sidebar li {{ margin: 2px 0; }}
.docs-sidebar a {{
  display: block; padding: 6px 12px; border-radius: 6px; color: var(--text-secondary);
  text-decoration: none; font-size: 0.85rem; transition: background 0.15s, color 0.15s;
}}
.docs-sidebar a:hover {{ background: var(--bg-card); color: var(--accent); text-decoration: none; }}
.docs-sidebar a.active {{ background: var(--accent-bg); color: var(--accent); font-weight: 500; }}

/* Responsive */
@media (max-width: 768px) {{
  .post-layout {{ grid-template-columns: 1fr; }}
  .toc-sidebar {{ display: none; }}
  .hero h1 {{ font-size: 1.5rem; }}
  .landing-hero h1 {{ font-size: 1.8rem; }}
  .docs-layout {{ grid-template-columns: 1fr; }}
  .docs-sidebar {{ display: none; }}
  .post-grid {{ grid-template-columns: 1fr; }}
  .featured {{ padding: 18px; flex-direction: column; gap: 14px; }}
  .featured .featured-cover {{ width: 100%; max-height: 200px; }}
  .author-card {{ flex-direction: column; text-align: center; }}
}}
@media (max-width: 600px) {{
  .container {{ padding: 0 16px; }}
  .article h1 {{ font-size: 1.4rem; }}
  .article h2 {{ font-size: 1.2rem; }}
}}
"""


# --- Page Templates ---

BASE_TEMPLATE = """<!DOCTYPE html>
<html lang="{lang}">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} — {site_name}</title>
  <meta name="description" content="{description}">
  <meta property="og:title" content="{title}">
  <meta property="og:description" content="{description}">
  <meta property="og:type" content="article">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{title}">
  <meta name="twitter:description" content="{description}">
  <link rel="stylesheet" href="{root}site.css">
  <link rel="alternate" type="application/atom+xml" title="{site_name} Feed" href="{root}atom.xml">
  {extra_head}
</head>
<body>
  <header class="site-header">
    <div class="container" style="max-width:900px">
      <a href="{root}index.html" class="site-name">{site_name}</a>
      <nav class="site-nav">
        {nav_links}
        <button class="search-toggle" onclick="openSearch()" title="Search" aria-label="Search">&#128269;</button>
        <button class="theme-toggle" onclick="toggleTheme()" title="Toggle theme" aria-label="Toggle theme">&#9790;</button>
      </nav>
    </div>
  </header>
  <div class="search-overlay" id="search-overlay" onclick="if(event.target===this)closeSearch()">
    <div class="search-box">
      <input class="search-input" id="search-input" placeholder="Search posts..." oninput="doSearch(this.value)" autofocus>
      <div class="search-results" id="search-results"></div>
    </div>
  </div>
  <main class="container">
    {content}
  </main>
  <footer class="site-footer">
    <div class="container">
      {cross_site_html}
      Powered by <a href="https://github.com/KevinBean/emptyos-site">EmptyOS</a>{rss_link}
    </div>
  </footer>
  <script>
  // Theme toggle
  (function(){{
    try{{var t=localStorage.getItem('site-theme');if(t==='alt-theme')document.body.classList.add('alt-theme');}}catch(e){{}}
  }})();
  function toggleTheme(){{
    document.body.classList.toggle('alt-theme');
    try{{localStorage.setItem('site-theme',document.body.classList.contains('alt-theme')?'alt-theme':'dark');}}catch(e){{}}
    var btn=document.querySelector('.theme-toggle');
    btn.innerHTML=document.body.classList.contains('alt-theme')?'&#9788;':'&#9790;';
  }}
  // Search
  var _searchIndex=null;
  function openSearch(){{
    document.getElementById('search-overlay').classList.add('open');
    document.getElementById('search-input').focus();
  }}
  function closeSearch(){{
    document.getElementById('search-overlay').classList.remove('open');
  }}
  document.addEventListener('keydown',function(e){{
    if((e.ctrlKey||e.metaKey)&&e.key==='k'){{e.preventDefault();openSearch();}}
    if(e.key==='Escape')closeSearch();
  }});
  function _countOcc(hay,term){{
    if(!hay||!term)return 0;
    var n=0,i=hay.indexOf(term);
    while(i>=0){{n++;i=hay.indexOf(term,i+term.length);}}
    return n;
  }}
  function doSearch(q){{
    if(!_searchIndex){{
      var searchUrl='{root}search-index.json';
      if(location.pathname.indexOf('/publish/api/')>=0)searchUrl='/publish/api/site-file?path=search-index.json';
      fetch(searchUrl).then(function(r){{return r.json();}}).then(function(d){{_searchIndex=d;doSearch(q);}});
      return;
    }}
    var el=document.getElementById('search-results');
    if(!q.trim()){{el.innerHTML='';return;}}
    // Ranked search: multi-term (AND), field-weighted (title x5, tags x3, summary x2, body x1),
    // full-phrase-in-title bonus; sort by score desc then date desc.
    var lq=q.toLowerCase();
    var terms=lq.split(/\\s+/).filter(function(t){{return t;}});
    var scored=[];
    for(var i=0;i<_searchIndex.length;i++){{
      var p=_searchIndex[i];
      var title=(p.title||'').toLowerCase();
      var tags=(p.tags||[]).join(' ').toLowerCase();
      var summary=(p.summary||'').toLowerCase();
      var body=(p.body_preview||'').toLowerCase();
      var score=0,allPresent=true;
      for(var t=0;t<terms.length;t++){{
        var h=_countOcc(title,terms[t])*5+_countOcc(tags,terms[t])*3+_countOcc(summary,terms[t])*2+_countOcc(body,terms[t]);
        if(h===0){{allPresent=false;break;}}
        score+=h;
      }}
      if(!allPresent)continue;
      if(title.indexOf(lq)>=0)score+=10;
      scored.push({{p:p,s:score}});
    }}
    scored.sort(function(a,b){{return b.s!==a.s?b.s-a.s:(b.p.date||'').localeCompare(a.p.date||'');}});
    var hits=scored.slice(0,8).map(function(x){{return x.p;}});
    el.innerHTML=hits.map(function(p){{
      var href=p.type==='page'?('{root}'+p.slug+'.html'):('{root}'+'posts/'+p.slug+'.html');
      return '<a class="search-result" href="'+href+'">'
        +'<div class="search-result-title">'+p.title+'</div>'
        +'<div class="search-result-summary">'+p.date+' &middot; '+(p.summary||'').substring(0,100)+'</div></a>';
    }}).join('')||'<div style="padding:12px;color:var(--text-muted);font-size:0.85rem">No results</div>';
  }}
  </script>
</body>
</html>"""


PAGE_CONTENT = """<article>
  <h1 style="font-size:1.8rem;color:var(--text-heading);margin-bottom:20px;font-weight:700">{title}</h1>
  <div class="article">
    {body}
  </div>
</article>"""


DOCS_PAGE_CONTENT = """<div class="docs-layout">
  <nav class="docs-sidebar">
    <div class="sidebar-title">Documentation</div>
    <ul>
{sidebar_links}
    </ul>
  </nav>
  <article>
    <h1 style="font-size:1.8rem;color:var(--text-heading);margin-bottom:20px;font-weight:700">{title}</h1>
    <div class="article">
      %%BODY%%
    </div>
  </article>
</div>"""


LANDING_CONTENT = """<div class="landing-hero">
  <h1>{title}</h1>
  <div class="tagline">{tagline}</div>
  <div class="landing-cta">{cta_html}</div>
  {hero_note_html}
</div>

{metrics_html}

{editions_html}

{gallery_html}

<div class="feature-grid">
{feature_cards}
</div>

{contact_html}

{blog_preview_html}"""


FEATURE_CARD = """<div class="feature-card">
  {icon_html}
  <h3>{heading}</h3>
  <div class="fc-body">%%BODY%%</div>
</div>"""


CONTACT_BAND = """<section class="contact-band">
  <div class="contact-heading">{heading}</div>
  <div class="landing-cta">{buttons}</div>
</section>"""


EDITIONS_BAND = """<section class="editions-band">
  <div class="editions-core">
    <div class="editions-core-eyebrow">One infrastructure</div>
    <div class="editions-core-title">kernel &middot; 16 capabilities &middot; event bus</div>
    <div class="editions-core-sub">{intro}</div>
  </div>
  <div class="editions-fan" aria-hidden="true"></div>
{groups}
  <div class="editions-foot"><a href="editions.html">See how the editions split &rarr;</a></div>
</section>"""


EDITIONS_GROUP = """  <div class="editions-group">
    <div class="editions-group-label">{label}</div>
    <div class="editions-grid{grid_mod}">
{tiles}
    </div>
  </div>"""


BLOG_PREVIEW = """<div class="blog-preview">
  <div class="section-heading">Latest Updates</div>
  <div class="post-grid">
{post_cards}
  </div>
  <div class="blog-viewall"><a href="blog.html">View all writing &rarr;</a></div>
</div>"""


INDEX_CONTENT = """<div class="hero">
  {avatar_html}
  <h1>{author_name}</h1>
  <div class="tagline">{site_description}</div>
  <div class="social">{social_html}</div>
</div>

{featured_html}

<div class="section-heading">Recent Posts</div>
<div class="post-grid">
{post_cards}
</div>"""


# Opt-in EE-prominent hero. Rendered instead of INDEX_CONTENT when the site
# config sets `hero_role`. Keeps eos.binbian.net / future blog sites on the
# legacy hero unless they explicitly opt in.
INDEX_CONTENT_PRO = """<div class="hero-pro">
  {avatar_html}
  <div class="name">{author_name}</div>
  <div class="social social-inline">{social_html}</div>
  <div class="role">{hero_role}</div>
  <div class="summary">{hero_summary}</div>
  {stats_html}
  {cta_html}
  {secondary_html}
</div>

{featured_html}

<div class="section-heading">Recent Posts</div>
<div class="post-grid">
{post_cards}
</div>"""


HERO_STAT = """<div class="stat"><strong>{value}</strong>{label}</div>"""


FEATURED_POST = """<a href="posts/{slug}.html" style="text-decoration:none" class="featured">
  {cover_html}
  <div class="featured-text">
    <div class="label">Featured</div>
    <div class="title">{title}</div>
    <div class="summary">{summary}</div>
    <div class="meta">{date} &middot; {reading_time} min read {tags_html}</div>
  </div>
</a>"""


POST_CARD = """<a href="posts/{slug}.html" class="post-card" style="text-decoration:none;display:block">
  {cover_html}
  <div class="card-title">{title}</div>
  <div class="card-meta">{date} &middot; {reading_time} min</div>
  <div class="card-summary">{summary}</div>
  <div class="card-tags">{tags_html}</div>
</a>"""


POST_ITEM = """<li class="post-item">
  <a href="posts/{slug}.html" class="post-title">{title}</a>
  <div class="post-meta">{date} {tags_html}</div>
  <div class="post-summary">{summary}</div>
</li>"""


POST_CONTENT = """<article>
  {lang_switch_html}
  {ai_notice_html}
  <div class="post-layout">
    <div>
      <div style="margin-bottom:24px">
        <h1 style="font-size:1.8rem;color:var(--text-heading);margin-bottom:8px">{title}</h1>
        <div class="post-meta">{date} &middot; {reading_time} min read {tags_html}</div>
      </div>
      <div class="article">
        {body}
      </div>
      {author_card_html}
      {backlinks_html}
      {related_html}
      <div style="margin-top:32px;padding-top:16px;border-top:1px solid var(--border)">
        <a href="{root}index.html">&larr; Back to posts</a>
      </div>
    </div>
    {toc_html}
  </div>
</article>"""


TOC_SIDEBAR = """<aside class="toc-sidebar">
  <div class="toc-title">Contents</div>
  {toc}
</aside>"""


AUTHOR_CARD = """<div class="author-card">
  {avatar_img}
  <div class="author-info">
    <div class="author-name">{name}</div>
    <div class="author-bio">{bio}</div>
    <div class="author-links">{links_html}</div>
  </div>
</div>"""


LANG_SWITCHER = """<div class="lang-switch">
{links}
</div>"""


AI_NOTICE = """<div class="ai-notice">
  &#129302; This post was AI-translated from {original_lang}.
  <a href="{original_url}">Read the original</a>
</div>"""


RELATED_POSTS = """<div style="margin-top:48px;padding-top:20px;border-top:1px solid var(--border)">
  <h3 style="font-size:1rem;color:var(--text-heading);margin-bottom:12px">Related Posts</h3>
  <ul class="post-list" style="margin:0">
{items}
  </ul>
</div>"""


# Backlinks ("Linked mentions") — notes that link to this one via [[wikilinks]].
# The signature digital-garden feature (Quartz / Obsidian Publish parity).
BACKLINKS = """<div class="backlinks">
  <h3 class="backlinks-title">&#128279; Linked mentions <span class="backlinks-count">{count}</span></h3>
  <ul class="backlinks-list">
{items}
  </ul>
</div>"""


BACKLINK_ITEM = """<li class="backlink-item">
  <a href="{root}{href}" class="backlink-link">{title}</a>
  <div class="post-meta">{date}</div>
</li>"""


RELATED_POST_ITEM = """<li class="post-item" style="padding:10px 0">
  <a href="{root}posts/{slug}.html" class="post-title" style="font-size:0.95rem">{title}</a>
  <div class="post-meta">{date}</div>
</li>"""


RSS_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>{site_name}</title>
  <subtitle>{site_description}</subtitle>
  <link href="{site_url}" rel="alternate"/>
  <link href="{site_url}atom.xml" rel="self"/>
  <updated>{updated}</updated>
  <id>{site_url}</id>
  <author><name>{author}</name></author>
{entries}
</feed>"""


RSS_ENTRY = """  <entry>
    <title>{title}</title>
    <link href="{site_url}posts/{slug}.html"/>
    <id>{site_url}posts/{slug}.html</id>
    <updated>{date}T00:00:00Z</updated>
    <summary>{summary}</summary>
  </entry>"""


TAG_PAGE_CONTENT = """<h1 style="font-size:1.4rem;color:var(--text-heading);margin-bottom:24px">
  Posts tagged <span class="tag" style="font-size:0.9rem">{tag}</span>
</h1>
<ul class="post-list">
{post_items}
</ul>
<div style="margin-top:24px">
  <a href="{root}tags.html">&larr; All tags</a>
</div>"""


TAGS_INDEX_CONTENT = """<h1 style="font-size:1.4rem;color:var(--text-heading);margin-bottom:24px">Tags</h1>
<div style="display:flex;flex-wrap:wrap;gap:8px">
{tag_links}
</div>"""


TAG_LINK = """<a href="tags/{slug}.html" class="tag" style="font-size:0.85rem;padding:4px 14px">{name} ({count})</a>"""


# Language name mapping
LANG_NAMES = {
    "en": "English",
    "zh": "中文",
    "ja": "日本語",
    "ko": "한국어",
    "es": "Español",
    "fr": "Français",
    "de": "Deutsch",
    "pt": "Português",
    "ru": "Русский",
    "ar": "العربية",
    "hi": "हिन्दी",
    "it": "Italiano",
}
