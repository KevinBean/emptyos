"""Shared scenario primitives — parallel fan-out, run-record persistence.

A `Scenario` is a callable that takes `(app, company_id, prompt, mode)` and
returns a dict (the saved run record). Mode is `"headless"` or `"in-room"`.

Run records persist as JSON files at `data/apps/company/runs/<run_id>.json`.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from emptyos.sdk.do_token import extract_do_tokens


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_run_id() -> str:
    return f"run-{uuid.uuid4().hex[:10]}"


def runs_dir(app) -> Path:
    d = app.data_dir / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_run(app, record: dict) -> None:
    p = runs_dir(app) / f"{record['id']}.json"
    p.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")


def load_run(app, run_id: str) -> dict | None:
    p = runs_dir(app) / f"{run_id}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_runs(app, company_id: str | None = None) -> list[dict]:
    out: list[dict] = []
    for f in runs_dir(app).glob("run-*.json"):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if company_id and r.get("company_id") != company_id:
            continue
        out.append({
            "id": r.get("id"),
            "scenario": r.get("scenario"),
            "company_id": r.get("company_id"),
            "prompt": (r.get("prompt") or "")[:120],
            "mode": r.get("mode"),
            "started": r.get("started"),
            "completed": r.get("completed"),
            "worker_count": len(r.get("responses") or []),
            "parent_run_id": r.get("parent_run_id") or "",
        })
    out.sort(key=lambda r: r.get("started", ""), reverse=True)
    return out


# ── Shared [DO:] action extraction ─────────────────────────────────
#
# Personas emit `[DO:app.method({...})]` tokens inline in their replies.
# Mirrors `rooms._gate_server_actions` so the review-gate paradigm
# (`.claude/rules/proposed-action.md`) covers every scenario uniformly —
# user reviews each proposed action as a card and clicks Apply or Reject.
#
# Allowlist of deliverable verbs personas may use. The list is also
# embedded into each scenario's user prompt so the model knows what's
# available. To add a verb: add a row here AND a line in the scenario
# prompt's allowlist block. No allowlist enforcement at parse time —
# `apply_pending` validates by attempting the call_app and surfacing
# failures as failed actions. The list is a hint, not a wall.
# Each row is (verb, args_example, description). The verb names are the
# PUBLIC methods callable via `app.call_app(app_id, method, **args)` — NOT
# the @web_route-decorated `api_*` handlers (those take a Request object
# and aren't directly dispatchable). The two are usually thin wrappers
# around each other; this list tracks the underlying methods.
DELIVERABLE_VERBS = [
    ("task.add", '{"text":"..."}', "One-shot todo into inbox"),
    ("projects.add_task_to_project", '{"project_id":"...","text":"..."}', "Scoped task"),
    ("kb.create_doc", '{"title":"...","paragraphs":[{"title":"...","content":"markdown..."}]}', "Short report as a KB doc (paragraphs use title+content)"),
    ("reports.create_report", '{"title":"...","template":"report","project_id":""}', "Templated long-form report scaffold"),
    ("publish.save_draft", '{"title":"...","content":"...markdown..."}', "Blog/site draft into publish source"),
    ("ppt.create_deck", '{"title":"...","outline":"..."}', "Deck with optional outline"),
    ("ppt.plan_deck", '{"title":"...","outline":"...","audience":"...","duration_min":5}', "AI-planned deck outline"),
    # publish.deploy is the canonical impact-shaped review-gate candidate
    # per .claude/rules/proposed-action.md — irreversible force-push to
    # gh-pages. ALWAYS gated; never autopilot. Personas may propose it
    # after a save_draft to "ship the post"; the user reviews the diff
    # implicitly via the pending card before clicking Apply.
    ("publish.deploy", '{}', "RUN THE WEBSITE: build + push site to gh-pages (review-gated, irreversible)"),
]


def render_deliverable_allowlist() -> str:
    """Render the deliverable verb list as a prompt-friendly block."""
    lines = ["Available [DO:] verbs (use only what fits your role):"]
    for verb, args, desc in DELIVERABLE_VERBS:
        lines.append(f"  - [DO:{verb}({args})]  # {desc}")
    return "\n".join(lines)


def pending_dir(app) -> Path:
    d = app.data_dir / "pending"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_pending(app, action: dict) -> None:
    p = pending_dir(app) / f"{action['id']}.json"
    p.write_text(json.dumps(action, indent=2, ensure_ascii=False), encoding="utf-8")


def extract_do_actions(text: str, *, run_id: str, source: dict) -> tuple[str, list[dict]]:
    """Strip `[DO:app.method({...})]` tokens from text; return (cleaned, [actions]).

    Thin wrapper over `emptyos.sdk.do_token.extract_do_tokens` that anchors
    every action to a scenario run via `run_id`. Callers persist via
    save_pending() and attach the ids to the message for UI review-gate
    cards. See `.claude/rules/proposed-action.md`.
    """
    return extract_do_tokens(text, source_actor=source, context={"run_id": run_id})


def gate_responses(app, responses: list[dict], *, run_id: str) -> list[dict]:
    """Run extract_do_actions on every response in-place; persist pending
    actions to disk; attach `pending: [id, ...]` to each response.
    Returns the flat list of all pending actions across responses.
    """
    all_pending: list[dict] = []
    for r in responses:
        cleaned, pending = extract_do_actions(
            r.get("response", ""),
            run_id=run_id,
            source={
                "type": "worker",
                "worker_id": r.get("worker_id"),
                "name": r.get("name"),
                "role": r.get("role"),
            },
        )
        r["response"] = cleaned
        r["pending"] = [p["id"] for p in pending]
        for p in pending:
            save_pending(app, p)
        all_pending.extend(pending)
    return all_pending


async def org_context_suffix(app, org: dict, *, max_assets_chars: int = 8000) -> str:
    """Compose a `system_suffix` injected into every per-member fan-out call.

    Pulls the org's body fields (mission/vision/values/culture/roles) and any
    resolved `assets:` (via app.list_assets) into one string. Empty fields
    are skipped — they contribute nothing. Asset bodies are concatenated
    until `max_assets_chars` is hit, then truncated; this keeps token cost
    bounded regardless of how many assets a user links.

    Returns "" when the org has nothing to add — fan_out_think then leaves
    the member's system_prompt untouched.
    """
    if not org:
        return ""
    parts: list[str] = []
    name = (org.get("name") or "").strip()
    oid = (org.get("id") or "").strip()
    if name:
        parts.append(f"You work at {name} ({oid}).")
    for field in ("mission", "vision", "values", "culture", "roles"):
        v = org.get(field)
        if isinstance(v, (list, tuple)):
            v = ", ".join(str(x).strip() for x in v if str(x).strip())
        v = (v or "").strip() if isinstance(v, str) else ""
        if v:
            parts.append(f"{field.title()}: {v}")

    # Pull resolved assets. list_assets is graceful — empty list when no
    # assets, no kb, or no list_assets method on the app (defensive guard
    # for older app builds; safe to remove once shipped).
    try:
        assets = await app.list_assets(oid) if hasattr(app, "list_assets") else []
    except Exception:
        assets = []
    if assets:
        parts.append("\n## Linked assets (reference for context, do not quote verbatim):")
        used = 0
        for a in assets:
            title = a.get("title") or a.get("ref") or "(untitled)"
            kind = a.get("kind") or "asset"
            body = (a.get("body") or "").strip()
            if not body:
                continue
            block = f"### {title} ({kind})\n{body}\n"
            if used + len(block) > max_assets_chars:
                break
            parts.append(block)
            used += len(block)
    return "\n\n".join(parts)


async def fan_out_think(
    app,
    workers: list[dict],
    build_prompt: Callable[[dict], str],
    *,
    system_suffix: str = "",
    parallel: bool = True,
    timeout_s: float = 120.0,
    temperature: float | None = None,
) -> list[dict]:
    """Call `app.think()` once per worker. Each worker's `system_prompt` is
    used as the `system=` arg; `build_prompt(worker)` produces the user
    message. Returns one dict per worker (`{worker_id, name, role, response,
    provenance, error?}`), in input order.

    `parallel=True` runs all workers concurrently with asyncio.gather. The
    provenance dict is captured per call via `app.last_provenance()`.
    Errors are caught and surfaced as `error` strings — one bad worker
    doesn't poison the run.
    """

    async def one(worker: dict) -> dict:
        sys_prompt = (worker.get("system_prompt") or "").strip()
        if system_suffix:
            sys_prompt = (sys_prompt + "\n\n" + system_suffix).strip()
        user_msg = build_prompt(worker)
        kwargs: dict[str, Any] = {"domain": "text"}
        if sys_prompt:
            kwargs["system"] = sys_prompt
        if worker.get("model"):
            kwargs["model"] = worker["model"]
        if temperature is not None:
            kwargs["temperature"] = temperature
        out: dict = {
            "worker_id": worker.get("id") or worker.get("file") or "",
            "name": worker.get("name") or worker.get("role") or "worker",
            "role": worker.get("role") or "",
            "emoji": worker.get("emoji") or "",
        }
        try:
            response = await asyncio.wait_for(
                app.think(user_msg, **kwargs), timeout=timeout_s
            )
            out["response"] = response if isinstance(response, str) else str(response)
            out["provenance"] = app.last_provenance()
        except asyncio.TimeoutError:
            out["response"] = ""
            out["error"] = f"timeout after {int(timeout_s)}s"
        except Exception as e:
            out["response"] = ""
            out["error"] = str(e)[:200]
        return out

    if parallel:
        return await asyncio.gather(*(one(w) for w in workers))
    results: list[dict] = []
    for w in workers:
        results.append(await one(w))
    return results


async def maybe_digest(
    app,
    *,
    title: str,
    responses: list[dict],
    prompt: str,
    system: str,
    temperature: float = 0.3,
) -> dict:
    """Optionally run one extra think() to synthesise the worker responses
    into a digest. Returns `{text, provenance}` or `{text: "", provenance: {}}`
    when disabled or no responses to digest.
    """
    enabled = bool(app.setting("company.digest_enabled", True))
    usable = [r for r in responses if r.get("response") and not r.get("error")]
    if not enabled or not usable:
        return {"text": "", "provenance": {}}

    lines = [f"# {title}", "", f"**Original prompt:** {prompt}", "", "## Worker responses", ""]
    for r in usable:
        header = f"### {r.get('name')} ({r.get('role')})"
        lines.append(header)
        lines.append(r["response"].strip())
        lines.append("")
    digest_prompt = "\n".join(lines) + "\n\nSynthesise these viewpoints. Surface agreements, disagreements, and the strongest recommendation."

    try:
        text = await app.think(digest_prompt, system=system, domain="text", temperature=temperature)
        return {
            "text": text if isinstance(text, str) else str(text),
            "provenance": app.last_provenance(),
        }
    except Exception as e:
        return {"text": "", "provenance": {}, "error": str(e)[:200]}
