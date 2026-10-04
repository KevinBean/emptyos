"""Distill a recorded work-trace into a reusable Recipe draft — the pure parse
layer of Record & Replay (the ``replay`` app).

The ``think()`` call lives in the app (``apps/public/standard/replay/distill.py``);
this module owns the prompt contract (:data:`RECIPE_DISTILL_SYSTEM`) and the pure,
never-raising validator (:func:`parse_recipe_draft`), mirroring
``BaseApp.propose_kb_extractions``' parse pattern so it unit-tests without a
daemon.

A recipe is an *appearance* of what a session did, never ground truth
(three-natures lens, ``.claude/rules/three-natures-lens.md``):

  * Every ``verb`` step is pinned to the LIVE verb registry (``known_verbs``). A
    step naming a verb the registry doesn't know is dropped with a warning — an
    unreplayable recipe is worse than a shorter one.
  * Template refs are restricted to declared inputs + EARLIER steps; a step whose
    refs don't resolve is dropped (and the cascade renumbers cleanly).
  * Best-effort by contract: a malformed model reply yields ``(None, [reason])``
    rather than raising.

Template grammar (the only forms; richer expressions are rejected at distill so
they never masquerade as real values):

  * ``{input.<name>}``                  — a recipe input
  * ``{step.<n>.output}``               — the whole artifact of earlier step ``n``
  * ``{step.<n>.output.<field>}``       — one-level field of that artifact
"""

from __future__ import annotations

import re
from typing import Iterable

from emptyos.sdk.utils import parse_llm_json

# ── Template grammar (single source of truth; shared.py's resolver imports these) ──

INPUT_TOKEN_RE = re.compile(r"\{input\.([A-Za-z0-9_]+)\}")
STEP_TOKEN_RE = re.compile(r"\{step\.(\d+)\.output(?:\.([A-Za-z0-9_]+))?\}")
WHOLE_TOKEN_RE = re.compile(
    r"^\{(?:input\.[A-Za-z0-9_]+|step\.\d+\.output(?:\.[A-Za-z0-9_]+)?)\}$"
)

_VERB_RE = re.compile(r"^[a-z][a-z0-9_-]*\.[a-z][a-z0-9_]*$")


def iter_template_refs(s: str) -> tuple[set[str], set[int]]:
    """Return ``(input_names, step_numbers)`` referenced by template tokens in ``s``."""
    if not isinstance(s, str):
        return set(), set()
    inputs = {m.group(1) for m in INPUT_TOKEN_RE.finditer(s)}
    steps = {int(m.group(1)) for m in STEP_TOKEN_RE.finditer(s)}
    return inputs, steps


RECIPE_DISTILL_SYSTEM = """You are distilling a recorded work session into a REUSABLE, REPLAYABLE recipe.

A recipe captures the *workflow* a person performed — its goal, the steps, the
rules, and how to verify it worked — so it can be run again later with fresh
inputs. You are watching a demonstration and writing down the procedure, not
transcribing keystrokes.

Return ONE JSON object, no prose around it, no markdown fences:
{
  "name": "short imperative title (<= 8 words)",
  "goal": "one sentence: what running this achieves",
  "when_to_use": "one sentence: the situation that calls for it",
  "inputs": [
    {"name": "snake_case", "label": "Human label", "type": "string|number|boolean",
     "required": true, "default": ""}
  ],
  "steps": [
    {"kind": "think", "prompt": "...", "domain": "text|code|reason",
     "why": "...", "expects_json": false},
    {"kind": "verb", "verb": "<app>.<method>", "args": {"k": "v or template"},
     "why": "..."}
  ],
  "verify": ["an observable thing that is true if the recipe succeeded"]
}

HARD RULES:
- Parameterize the parts that VARY between runs as `inputs`; reference them in
  step args/prompts via {input.<name>}.
- Reference an earlier step's result via {step.<n>.output} or
  {step.<n>.output.<field>} (n is the 1-based step number; field needs that
  step's "expects_json": true). No other template syntax — no expressions,
  loops, or conditionals. If a step needs real logic, make it a `think` step.
- A `verb` step MUST name a real `<app>.<method>` that occurred in the session.
  Do NOT invent verbs. The agent's own file/shell tools (Read, Edit, Write,
  Bash, Grep) are NOT verbs — capture that work as a `think` step describing the
  judgment, or omit it.
- Prefer a few meaningful steps over replaying every keystroke.
- NEVER embed credentials, absolute machine paths, tokens, or personal data —
  lift anything that varies into an input.
- Output ONLY the JSON object.
"""


# ── pure helpers ───────────────────────────────────────────────────────────


def _slug_token(s: str) -> str:
    s = (s or "").strip().lower()
    return re.sub(r"[^a-z0-9_]+", "_", s).strip("_")


def _short(x, n: int = 500) -> str | None:
    if x is None:
        return None
    s = str(x)
    return s[:n] if s else None


def _iter_str_values(obj) -> Iterable[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_str_values(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_str_values(v)


def _refs_ok(s: str, input_names: set[str], survivors: dict[int, int]) -> tuple[bool, str]:
    """True iff every template ref in ``s`` resolves to a declared input or an
    earlier surviving step (``survivors`` maps original-index → new-n)."""
    ins, steps = iter_template_refs(s)
    bad_in = ins - input_names
    if bad_in:
        return False, f"references undeclared input(s) {sorted(bad_in)}"
    bad_step = {n for n in steps if n not in survivors}
    if bad_step:
        return False, f"references missing/later step(s) {sorted(bad_step)}"
    return True, ""


def _remap(s: str, survivors: dict[int, int]) -> str:
    """Rewrite ``{step.<oldn>...}`` to the renumbered surviving step number."""
    if not isinstance(s, str):
        return s

    def sub(m):
        old = int(m.group(1))
        new = survivors.get(old, old)
        field = m.group(2)
        return f"{{step.{new}.output.{field}}}" if field else f"{{step.{new}.output}}"

    return STEP_TOKEN_RE.sub(sub, s)


def _remap_args(args, survivors: dict[int, int]):
    if isinstance(args, dict):
        return {k: _remap_args(v, survivors) for k, v in args.items()}
    if isinstance(args, list):
        return [_remap_args(v, survivors) for v in args]
    if isinstance(args, str):
        return _remap(args, survivors)
    return args


def parse_recipe_draft(
    raw, *, known_verbs: set[str] | None
) -> tuple[dict | None, list[str]]:
    """Validate a distill reply into a recipe draft. Never raises.

    Returns ``(draft, warnings)`` or ``(None, warnings)`` when nothing usable
    survives. ``known_verbs`` is the live registry's verb set; pass ``None`` to
    skip the registry check (tests). Steps are validated forward (refs may only
    point at earlier survivors) and renumbered 1..M after pruning.
    """
    warnings: list[str] = []
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = parse_llm_json(raw if isinstance(raw, str) else "", fallback=[])
        except ValueError:
            data = None
    if not isinstance(data, dict):
        return None, ["distill reply was not a JSON object"]

    name = str(data.get("name") or "").strip()
    if not name:
        return None, ["recipe has no name"]
    goal = str(data.get("goal") or "").strip()
    when_to_use = str(data.get("when_to_use") or "").strip()

    inputs: list[dict] = []
    input_names: set[str] = set()
    for raw_in in data.get("inputs") or []:
        if not isinstance(raw_in, dict):
            continue
        nm = _slug_token(str(raw_in.get("name") or ""))
        if not nm or nm in input_names:
            continue
        input_names.add(nm)
        inputs.append(
            {
                "name": nm,
                "label": str(raw_in.get("label") or nm).strip(),
                "type": raw_in["type"]
                if raw_in.get("type") in ("string", "number", "boolean")
                else "string",
                "required": bool(raw_in.get("required", False)),
                "default": raw_in.get("default", ""),
            }
        )

    survivors: list[dict] = []
    old_to_new: dict[int, int] = {}
    for oi, rs in enumerate(data.get("steps") or [], 1):
        if not isinstance(rs, dict):
            warnings.append(f"step {oi}: not an object — dropped")
            continue
        kind = rs.get("kind")
        if kind == "think":
            prompt = str(rs.get("prompt") or "").strip()
            if not prompt:
                warnings.append(f"step {oi}: think step missing prompt — dropped")
                continue
            ok, reason = _refs_ok(prompt, input_names, old_to_new)
            if not ok:
                warnings.append(f"step {oi}: {reason} — dropped")
                continue
            new_n = len(survivors) + 1
            old_to_new[oi] = new_n
            survivors.append(
                {
                    "n": new_n,
                    "kind": "think",
                    "prompt": _remap(prompt, old_to_new),
                    "domain": rs["domain"]
                    if rs.get("domain") in ("text", "code", "reason")
                    else "text",
                    "why": str(rs.get("why") or "").strip(),
                    "expects_json": bool(rs.get("expects_json", False)),
                    "recorded_output": _short(rs.get("recorded_output")),
                }
            )
        elif kind == "verb":
            verb = str(rs.get("verb") or "").strip()
            if not _VERB_RE.match(verb):
                warnings.append(f"step {oi}: malformed verb '{verb}' — dropped")
                continue
            if known_verbs is not None and verb not in known_verbs:
                warnings.append(f"step {oi}: unknown verb '{verb}' — dropped")
                continue
            args = rs["args"] if isinstance(rs.get("args"), dict) else {}
            bad = ""
            for v in _iter_str_values(args):
                ok, reason = _refs_ok(v, input_names, old_to_new)
                if not ok:
                    bad = reason
                    break
            if bad:
                warnings.append(f"step {oi}: {bad} — dropped")
                continue
            new_n = len(survivors) + 1
            old_to_new[oi] = new_n
            survivors.append(
                {
                    "n": new_n,
                    "kind": "verb",
                    "verb": verb,
                    "args": _remap_args(args, old_to_new),
                    "why": str(rs.get("why") or "").strip(),
                    "eligibility": rs["eligibility"]
                    if rs.get("eligibility") in ("stable", "gated", "never")
                    else "",
                    "recorded_args": rs["recorded_args"]
                    if isinstance(rs.get("recorded_args"), dict)
                    else {},
                    "recorded_output": _short(rs.get("recorded_output")),
                }
            )
        else:
            warnings.append(f"step {oi}: unknown kind '{kind}' — dropped")

    if not survivors:
        return None, warnings + ["no usable steps after validation"]

    verify = [str(x).strip() for x in (data.get("verify") or []) if str(x).strip()]
    draft = {
        "name": name,
        "goal": goal,
        "when_to_use": when_to_use,
        "inputs": inputs,
        "steps": survivors,
        "verify": verify,
    }
    return draft, warnings
