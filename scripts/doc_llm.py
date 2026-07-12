"""LLM-assisted enrichment for generated docs — best-effort, cached, optional.

The deterministic extractor (`doc_data.py`) provides the *facts*; this module
polishes the *prose* — a crisp one-line "what it does for the user" summary for
each app, used when a manifest description is thin or developer-flavoured.

Design guarantees (so the generators are reliable in CI / offline):
  - **Deterministic baseline always works.** With no daemon / no `--llm`, every
    app gets its manifest description as the summary. LLM is enrichment, never a
    dependency.
  - **Cached by content hash, in git.** Results live in `docs/doc-summaries.json`
    (TRACKED, not gitignored) so the polished `APPS.md` is reproducible from
    tracked inputs in any clean checkout — the `--check` gate can't false-fail
    on a missing cache. An app whose manifest didn't change is never re-sent →
    repeat runs make zero LLM calls.
  - **No vault content leaves the machine** (rule 19): only the manifest's own
    fields are sent, and the daemon's cloud-consent gate still applies.

LLM transport: `POST http://127.0.0.1:9000/assistant/api/chat` with
`context:false` (one-shot, no session, no vault context). Uses `127.0.0.1` (not
`localhost`) + Bearer `auth_token` per `.claude/rules/environment.md`. Any
failure (daemon down, no provider, timeout) silently falls back to baseline.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Tracked (NOT under gitignored data/) so APPS.md is reproducible in a clean checkout.
CACHE_FILE = ROOT / "docs" / "doc-summaries.json"
DAEMON = "http://127.0.0.1:9000"

_SUMMARY_PROMPT = (
    "You are writing a one-line catalog blurb for an app in EmptyOS, a local-first "
    "AI workspace. Given the app's id and developer description, write ONE plain "
    "sentence (max 14 words) describing what it does for the user. No marketing, no "
    "trailing period, no app id, no 'This app'. Just the capability.\n\n"
    "App id: {id}\nDescription: {desc}\n\nOne-line blurb:"
)


def _auth_token() -> str:
    try:
        with open(ROOT / "emptyos.toml", "rb") as f:
            return (tomllib.load(f).get("network", {}) or {}).get("auth_token", "") or ""
    except (OSError, tomllib.TOMLDecodeError):
        return ""


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")


def _hash(app: dict) -> str:
    sig = f"{app.get('id')}|{app.get('description')}"
    return hashlib.sha256(sig.encode("utf-8")).hexdigest()[:16]


def _baseline(app: dict) -> str:
    desc = (app.get("description") or "").strip().rstrip(".")
    # Drop a leading "<Name> — " prefix some manifests use.
    if " — " in desc[:40]:
        desc = desc.split(" — ", 1)[1]
    return desc


def _llm_one(prompt: str, token: str, timeout: float = 30.0) -> str:
    body = json.dumps({"message": prompt, "context": False, "use_tools": False}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{DAEMON}/assistant/api/chat", data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    text = (data.get("response") or "").strip()
    # Take the first non-empty line, strip surrounding quotes/bullets.
    for line in text.splitlines():
        line = line.strip().lstrip("-*• ").strip().strip('"').strip()
        if line:
            return line[:120]
    return ""


def enrich_apps(apps: list[dict], *, use_llm: bool = False) -> dict[str, dict]:
    """Return {app_id: {"summary": str, "source": "llm"|"cache"|"baseline"}}.

    Deterministic baseline for every app; LLM polish only for apps whose content
    hash isn't already cached, and only when `use_llm` is set and the daemon is
    reachable.
    """
    cache = _load_cache()
    token = _auth_token() if use_llm else ""
    out: dict[str, dict] = {}
    llm_ok = use_llm
    for app in apps:
        aid = app["id"]
        h = _hash(app)
        cached = cache.get(aid)
        if cached and cached.get("hash") == h and cached.get("summary"):
            out[aid] = {"summary": cached["summary"], "source": "cache"}
            continue
        summary = _baseline(app)
        source = "baseline"
        if llm_ok:
            try:
                blurb = _llm_one(_SUMMARY_PROMPT.format(id=aid, desc=summary or aid), token)
                if blurb:
                    summary = blurb
                    source = "llm"
                    cache[aid] = {"hash": h, "summary": summary}
            except Exception:
                # First failure → daemon unreachable / no provider; stop trying.
                llm_ok = False
        out[aid] = {"summary": summary, "source": source}
    if use_llm:
        _save_cache(cache)
    return out
