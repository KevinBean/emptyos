"""Shared browser/auth helpers for the UI-walk scripts (check-js-errors,
check-clickable, ui_walk_audit, ui_walk_round2).

This module is imported by committed scripts, so despite the leading underscore
it is NOT a private one-off — `.gitignore` carries an explicit
`!scripts/_eos_browser.py` exception for it. (It was originally gitignored by the
`scripts/_*.py` rule, which silently dropped it from the eos-ui-walk skill commit
and left every walker dying on `ModuleNotFoundError: _eos_browser`.)

Three names the walkers rely on:
  BASE                    daemon base URL (str), e.g. "http://127.0.0.1:9000"
  load_auth_token()       -> token str ("" when the daemon has no auth gate)
  eos_storage_state(tok)  -> Playwright storage_state dict (or None when no token)

BASE is environment-overridable so the same walkers can target a remote daemon
(Tailscale / LAN) without code edits:
  EOS_BASE / EOS_URL      full base URL    (e.g. http://<tailnet-ip>:9000)
  EOS_HOST + EOS_PORT     host + port parts

Token resolution order: EOS_AUTH_TOKEN env > emptyos.toml (network.auth_token,
then top-level auth_token). Never hardcode a token here.
"""
from __future__ import annotations

import os
import urllib.parse
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _resolve_base() -> str:
    base = os.environ.get("EOS_BASE") or os.environ.get("EOS_URL")
    if base:
        return base.rstrip("/")
    host = os.environ.get("EOS_HOST", "127.0.0.1")
    port = os.environ.get("EOS_PORT", "9000")
    return f"http://{host}:{port}"


BASE = _resolve_base()


def load_auth_token() -> str:
    """Auth token for the daemon, or "" when no gate is configured."""
    tok = os.environ.get("EOS_AUTH_TOKEN")
    if tok:
        return tok.strip()
    toml_path = REPO / "emptyos.toml"
    if toml_path.exists():
        try:
            import tomllib
            cfg = tomllib.loads(toml_path.read_text(encoding="utf-8"))
            tok = cfg.get("network", {}).get("auth_token") or cfg.get("auth_token") or ""
            if tok:
                return str(tok).strip()
        except Exception:
            pass
    return ""


def eos_storage_state(token: str | None) -> dict | None:
    """Playwright storage_state that authenticates against the daemon.

    The server's auth gate accepts the token via the `eos_session` cookie
    (emptyos/web/server.py). Returns None when there's no token so callers can
    fall back to a plain context (`new_context(storage_state=ss) if ss else ...`).
    """
    if not token:
        return None
    host = urllib.parse.urlparse(BASE).hostname or "127.0.0.1"
    return {
        "cookies": [
            {
                "name": "eos_session",
                "value": token,
                "domain": host,
                "path": "/",
                "expires": -1,          # session cookie
                "httpOnly": True,
                "secure": False,
                "sameSite": "Lax",
            }
        ],
        "origins": [],
    }


def embed_image_data_uri(path) -> "str | None":
    """Read an image file and return a base64 ``data:`` URI, or None if missing/unreadable.

    Shared by the UI-walk report renderers (``ui_walk_audit.py`` report-html +
    ``ui_walk_report.py``) so a screenshot can be inlined into a self-contained
    HTML artifact — no loose image files, openable straight in a browser.
    """
    import base64
    import mimetypes
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return None
    try:
        data = p.read_bytes()
    except Exception:  # noqa: BLE001 — a missing/locked shot must not crash the report
        return None
    mime = mimetypes.guess_type(str(p))[0] or "image/png"
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
