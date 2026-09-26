"""Machine-path resolution for the `scripts/mv/` toolkit.

The MV tools need a few machine-specific locations: ComfyUI's install (its
embedded python runs the ESRGAN upscaler; its `models/` folder holds the
upscale, InfiniteTalk, SyncNet and insightface weights) and the fonts the PIL
text renderer draws with. None of these may be hard-coded in tracked code
(CLAUDE.md rule 13), so every tool resolves them here, in one order:

    CLI flag  >  [mv_tools] in emptyos.toml  >  derived default  >  exit 2

Derived defaults: `comfy_root` from `[plugins.comfyui] launcher` (its folder),
then `comfy_python` = `<comfy_root>/python_embeded/python.exe` and
`models_dir` = `<comfy_root>/ComfyUI/models`; fonts by file name from the OS
font folders (Windows: system + per-user; macOS: /System/Library/Fonts and
/Library/Fonts). Linux keeps fonts in nested folders, so there a font must be
named in `[mv_tools.fonts]`.

`resolve()` drops a *derived* path that does not exist on disk (so a fresh
clone reads as "not configured"), but returns an explicit one as given.
`require()` then checks existence for every layer and names the layer in its
error, so a wrong path fails here with an actionable message instead of deep
inside ffmpeg or torch. A malformed emptyos.toml is reported as such, never
silently treated as empty.

Stdlib only, and it never imports the kernel.
"""
from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Font roles the text renderer uses, mapped to file names looked up in the OS
# font folders when [mv_tools.fonts] does not name a path.
FONT_DEFAULTS = {
    "zh_sans": "msjh.ttc",             # 微軟正黑 — album title cards, lyric subtitles
    "zh_serif": "NotoSerifSC-VF.ttf",  # vertical in-picture text cards
    "en_serif_italic": "georgiai.ttf",
    "en_sans": "segoeui.ttf",
}


def _toml(repo_root: Path = REPO_ROOT) -> dict:
    path = repo_root / "emptyos.toml"
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except OSError:
        return {}
    except tomllib.TOMLDecodeError as exc:
        print(f"MV tools: {path} is not valid TOML ({exc}). Fix it before "
              f"running the MV tools.", file=sys.stderr)
        raise SystemExit(2) from exc


def _font_dirs() -> list[Path]:
    if sys.platform.startswith("win"):
        dirs = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"]
        if os.environ.get("LOCALAPPDATA"):
            dirs.append(Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Windows" / "Fonts")
        return dirs
    if sys.platform == "darwin":
        return [Path("/System/Library/Fonts"), Path("/Library/Fonts")]
    return []


def _comfy_root(cfg: dict) -> tuple[Path | None, str]:
    mv = cfg.get("mv_tools") or {}
    if mv.get("comfy_root"):
        return Path(mv["comfy_root"]), "[mv_tools] comfy_root"
    launcher = str(((cfg.get("plugins") or {}).get("comfyui") or {}).get("launcher") or "").strip()
    if launcher:
        return Path(launcher).parent, "[plugins.comfyui] launcher"
    return None, ""


def _resolve(key: str, cli, cfg: dict) -> tuple[Path | None, str]:
    """(path, where it came from). A derived path missing on disk is (None, why)."""
    if cli:
        return Path(cli), "the command line"
    mv = cfg.get("mv_tools") or {}
    if key.startswith("font."):
        role = key.split(".", 1)[1]
        named = (mv.get("fonts") or {}).get(role)
        if named:
            return Path(named), f"[mv_tools.fonts] {role}"
        fname = FONT_DEFAULTS.get(role)
        for d in _font_dirs() if fname else []:
            if (d / fname).exists():
                return d / fname, "the OS font folder"
        return None, ""
    if mv.get(key):
        return Path(mv[key]), f"[mv_tools] {key}"
    root, root_src = _comfy_root(cfg)
    if root is None:
        return None, ""
    if key == "comfy_root":
        p = root
    elif key == "comfy_python":
        p = root / "python_embeded" / ("python.exe" if sys.platform.startswith("win") else "python")
    elif key == "models_dir":
        p = root / "ComfyUI" / "models"
    else:
        return None, ""
    if p.exists():
        return p, f"derived from {root_src}"
    return None, f"derived from {root_src} as {p}, which does not exist"


def resolve(key: str, cli: str | os.PathLike | None = None, *, cfg: dict | None = None) -> Path | None:
    """Resolve a machine path, or None when nothing usable is configured."""
    return _resolve(key, cli, _toml() if cfg is None else cfg)[0]


def setting(dotted: str, default=None, *, cfg: dict | None = None):
    """A plain value from ``[mv_tools]`` by dotted key (``infinitetalk.base``), or ``default``."""
    node = (_toml() if cfg is None else cfg).get("mv_tools") or {}
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def comfy_host(cli: str | None = None, *, cfg: dict | None = None) -> str:
    """ComfyUI's HTTP address: CLI > [mv_tools] comfy_host > [plugins.comfyui] host."""
    if cli:
        return cli.rstrip("/")
    cfg = _toml() if cfg is None else cfg
    host = ((cfg.get("mv_tools") or {}).get("comfy_host")
            or ((cfg.get("plugins") or {}).get("comfyui") or {}).get("host"))
    if not host:
        print("MV tools: no ComfyUI host. Pass --comfy-host, or set [plugins.comfyui] host "
              f"in {REPO_ROOT / 'emptyos.toml'}.", file=sys.stderr)
        raise SystemExit(2)
    return str(host).rstrip("/")


def require(key: str, cli: str | os.PathLike | None = None, *, cfg: dict | None = None) -> Path:
    """Resolve a path that must exist, or exit 2 saying which layer is wrong."""
    p, src = _resolve(key, cli, _toml() if cfg is None else cfg)
    if p is not None and p.exists():
        return p
    if p is not None:
        msg = f"'{key}' from {src} does not exist: {p}."
    else:
        hint = (f"[mv_tools.fonts] {key.split('.', 1)[1]} = \"<path to font file>\""
                if key.startswith("font.") else f"[mv_tools] {key} = \"<path>\"")
        why = f" (tried: {src})" if src else ""
        msg = (f"'{key}' is not configured{why}. Pass it on the command line, "
               f"or add {hint} to {REPO_ROOT / 'emptyos.toml'}.")
    print(f"MV tools: {msg}", file=sys.stderr)
    raise SystemExit(2)
