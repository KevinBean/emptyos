"""replay — module-level constants + pure helpers shared across helper modules.

Pure stdlib + a token-grammar import from ``emptyos.sdk.recipe_distill`` (the
single source of truth for the template syntax). No ``self``, no kernel, no IO —
so it loads standalone and unit-tests without a daemon.

Owns: the recipe note <-> draft round-trip (:func:`render_recipe_note` /
:func:`parse_recipe`), the SKILL.md renderer (:func:`render_skill_md`), and the
template resolver (:func:`resolve_template` / :func:`safe_resolve`) replay uses
to bind ``{input.X}`` / ``{step.N.output[.field]}`` against live values.
"""

from __future__ import annotations

import json
import re

from emptyos.sdk.recipe_distill import (
    INPUT_TOKEN_RE,
    STEP_TOKEN_RE,
    WHOLE_TOKEN_RE,
)

# The saved workflow artifact is a "replay". Its tag is deliberately NOT
# "recipe" — that collides with cooking-recipe notes in the user's vault
# (nutrition/meal-planner). "replay" is unique and matches the app name.
RECIPE_TAG = "replay"
RECIPE_DIR = "30_Resources/EmptyOS/replays"
SKILL_DIR = ".claude/skills"
# Sentinel a dry-run shows where a value isn't known until the run actually runs.
DRY_PLACEHOLDER = "‹computed at run time›"
UNRESOLVED_PLACEHOLDER = "‹?›"


class TemplateError(ValueError):
    """A template token couldn't be resolved against the given inputs/results."""


def slugify(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "recipe"


# ── template resolution ─────────────────────────────────────────────────────


def _lookup(kind: str, key: str, field: str | None, inputs: dict, results: dict):
    """Return ``(found, value)`` for one token. ``results`` is keyed ``step-<n>``."""
    if kind == "input":
        if key in (inputs or {}):
            return True, inputs[key]
        return False, None
    # step
    res_key = f"step-{key}"
    if res_key not in (results or {}):
        return False, None
    val = results[res_key]
    if field:
        if isinstance(val, dict) and field in val:
            return True, val[field]
        return False, None
    return True, val


def resolve_template(value, inputs: dict, results: dict, *, safe: bool = False):
    """Bind ``{input.X}`` / ``{step.N.output[.field]}`` tokens in ``value``.

    Recurses dicts/lists. A string that is exactly one token returns the TYPED
    value (a dict/number passes through untouched); a string with tokens mixed
    into text returns the substituted string. An unresolved token raises
    :class:`TemplateError` unless ``safe=True`` (then it becomes a placeholder —
    used by the dry-run preview, which must never raise).
    """
    if isinstance(value, dict):
        return {k: resolve_template(v, inputs, results, safe=safe) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_template(v, inputs, results, safe=safe) for v in value]
    if not isinstance(value, str):
        return value

    if WHOLE_TOKEN_RE.match(value):
        m = INPUT_TOKEN_RE.fullmatch(value)
        if m:
            found, val = _lookup("input", m.group(1), None, inputs, results)
        else:
            sm = STEP_TOKEN_RE.fullmatch(value)
            found, val = _lookup("step", sm.group(1), sm.group(2), inputs, results)
        if found:
            return val
        if safe:
            return UNRESOLVED_PLACEHOLDER
        raise TemplateError(f"unresolved template {value!r}")

    def repl_input(m):
        found, val = _lookup("input", m.group(1), None, inputs, results)
        if found:
            return str(val)
        if safe:
            return UNRESOLVED_PLACEHOLDER
        raise TemplateError(f"unresolved template {m.group(0)!r}")

    def repl_step(m):
        found, val = _lookup("step", m.group(1), m.group(2), inputs, results)
        if found:
            return str(val)
        if safe:
            return UNRESOLVED_PLACEHOLDER
        raise TemplateError(f"unresolved template {m.group(0)!r}")

    out = INPUT_TOKEN_RE.sub(repl_input, value)
    out = STEP_TOKEN_RE.sub(repl_step, out)
    return out


def safe_resolve(value, inputs: dict, results: dict):
    """:func:`resolve_template` that never raises (dry-run preview)."""
    return resolve_template(value, inputs, results, safe=True)


# ── recipe note <-> draft ───────────────────────────────────────────────────


_JSON_FENCE_RE = re.compile(r"```json\s*\n(.*?)\n```", re.S)


def _fm_scalar(v) -> str:
    """Encode one frontmatter string value to round-trip through
    ``emptyos.sdk.utils.parse_frontmatter`` (the reader ``VaultLibrary.detail``
    uses): bare when plain, else DOUBLE-quoted with ``\\``→``\\\\`` and
    ``"``→``\\"`` (exactly what parse_frontmatter reverses). We never use the
    single-quote/``''`` form — parse_frontmatter doesn't un-double it, which
    corrupts apostrophes. Machine JSON does NOT live in frontmatter (see
    :func:`render_recipe_note`), so values here are short prose only."""
    sv = str(v)
    if sv == "":
        return '""'
    if any(c in sv for c in ":#{}[]|>&*?!,\"'\\") or sv != sv.strip():
        return '"' + sv.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return sv


def render_recipe_note(draft: dict, *, source_session: str, source_trace: str, created: str) -> str:
    """Render a replay draft to the full ``.md`` note bytes (frontmatter + body).

    Machine data (``inputs``/``steps``/``verify``) lives in a fenced ```json``
    block in the BODY — parsed back by :func:`parse_recipe` via ``json.loads``,
    so there is no YAML-quoting round-trip to corrupt apostrophes/quotes (the
    bug from putting JSON in frontmatter). Frontmatter keeps only short fields +
    a ``steps_count`` for the list view. ``created`` is passed in so the function
    stays pure (no clock)."""
    name = str(draft.get("name") or "Untitled replay").strip()
    slug = slugify(name)
    inputs = draft.get("inputs") or []
    steps = draft.get("steps") or []
    verify = draft.get("verify") or []

    fm = {
        "id": slug,
        "name": name,
        "goal": str(draft.get("goal") or "").strip(),
        "when_to_use": str(draft.get("when_to_use") or "").strip(),
        "status": "draft",
        "author": "ai",
        "source_session": source_session or "",
        "source_trace": source_trace or "",
        "created": created,
        "updated": created,
        "steps_count": len(steps),
    }
    lines = ["---"]
    for k, v in fm.items():
        if v is None or v == "":
            continue
        if isinstance(v, int):
            lines.append(f"{k}: {v}")
        else:
            lines.append(f"{k}: {_fm_scalar(v)}")
    lines.append("tags:")
    lines.append(f"  - {RECIPE_TAG}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {name}")
    if fm["goal"]:
        lines.append("")
        lines.append(f"**Goal** — {fm['goal']}")
    if fm["when_to_use"]:
        lines.append(f"**When to use** — {fm['when_to_use']}")
    if inputs:
        lines.append("")
        lines.append("## Inputs")
        for i in inputs:
            req = " *(required)*" if i.get("required") else ""
            lines.append(f"- `{i.get('name')}` — {i.get('label') or i.get('name')}{req}")
    lines.append("")
    lines.append("## Steps")
    for s in steps:
        n = s.get("n")
        if s.get("kind") == "verb":
            head = f"{n}. **`{s.get('verb')}`**"
        else:
            head = f"{n}. **think** ({s.get('domain', 'text')})"
        why = s.get("why")
        lines.append(f"{head}{' — ' + why if why else ''}")
    if verify:
        lines.append("")
        lines.append("## Verification")
        for v in verify:
            lines.append(f"- [ ] {v}")
    # Machine-readable spec (the source of truth for replay) — fenced JSON.
    lines.append("")
    lines.append("## Spec")
    lines.append("<!-- machine-readable; edited by the Replay app -->")
    lines.append("```json")
    lines.append(json.dumps({"inputs": inputs, "steps": steps, "verify": verify}, ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def extract_spec(body: str) -> dict:
    """Pull ``{inputs, steps, verify}`` from the body's ```json fence. Never raises."""
    if not body:
        return {}
    m = _JSON_FENCE_RE.search(body)
    if not m:
        return {}
    try:
        data = json.loads(m.group(1))
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def parse_recipe(detail: dict) -> dict:
    """Decode a replay note ``detail`` (frontmatter fields + ``body``) into a
    recipe dict. Machine data comes from the body ```json fence; the rest from
    frontmatter. Never raises."""
    spec = extract_spec(detail.get("body") or "")
    return {
        "id": detail.get("id") or slugify(detail.get("name") or ""),
        "name": detail.get("name") or "",
        "goal": detail.get("goal") or "",
        "when_to_use": detail.get("when_to_use") or "",
        "status": detail.get("status") or "draft",
        "author": detail.get("author") or "",
        "source_session": detail.get("source_session") or "",
        "source_trace": detail.get("source_trace") or "",
        "created": detail.get("created") or "",
        "updated": detail.get("updated") or "",
        "inputs": spec.get("inputs") or [],
        "steps": spec.get("steps") or [],
        "verify": spec.get("verify") or [],
    }


def render_skill_md(draft: dict, *, source_session: str) -> str:
    """Render a recipe draft as a ``.claude/skills/`` SKILL.md draft — the
    conversation-mode reusable form (name + description frontmatter + prose)."""
    name = str(draft.get("name") or "Untitled").strip()
    slug = slugify(name)
    goal = str(draft.get("goal") or "").strip()
    when = str(draft.get("when_to_use") or "").strip()
    desc = goal or name
    if when:
        desc = f"{desc} Use when: {when}"
    lines = ["---", f"name: eos-{slug}", f"description: {_fm_scalar(desc)}", "---", ""]
    lines.append(f"# {name}")
    lines.append("")
    if goal:
        lines.append(f"**Goal** — {goal}")
    if when:
        lines.append(f"**When to use** — {when}")
    inputs = draft.get("inputs") or []
    if inputs:
        lines.append("")
        lines.append("## Inputs")
        for i in inputs:
            req = " (required)" if i.get("required") else ""
            lines.append(f"- `{i.get('name')}` — {i.get('label') or i.get('name')}{req}")
    lines.append("")
    lines.append("## Steps")
    for s in draft.get("steps") or []:
        n = s.get("n")
        if s.get("kind") == "verb":
            detail = f"Call `{s.get('verb')}`"
            args = s.get("args") or {}
            if args:
                detail += f" with `{json.dumps(args, ensure_ascii=False)}`"
        else:
            detail = f"Think ({s.get('domain', 'text')}): {s.get('prompt', '')}"
        why = s.get("why")
        lines.append(f"{n}. {detail}{' — ' + why if why else ''}")
    verify = draft.get("verify") or []
    if verify:
        lines.append("")
        lines.append("## Verify")
        for v in verify:
            lines.append(f"- {v}")
    if source_session:
        lines.append("")
        lines.append(f"*Distilled from agent session `{source_session}`.*")
    lines.append("")
    return "\n".join(lines)
