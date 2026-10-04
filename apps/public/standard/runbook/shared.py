"""Runbook — module-level constants + pure helpers shared across helper modules.

Extracted from app.py so the engine / blocks / routes modules can import these
directly without cycling through the spine `.app` module.

Pure functions only — no `self`, no kernel access, no I/O. Owns the on-disk
``eos-block`` fence format: parse, serialize, and `{{template}}` rendering. The
runbook note is canonical human-editable markdown in the vault; this module is
the single place that knows how a typed block round-trips to/from text.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field

# ── Vault locations + conventions ──────────────────────────────────────────
RUNBOOK_DIR = "30_Resources/EmptyOS/runbook"
OUTPUTS_DIR = RUNBOOK_DIR + "/outputs"          # AI-authored artifacts (author: ai)
RUNBOOK_TAG = "runbook"

# ── Block taxonomy ─────────────────────────────────────────────────────────
BLOCK_TYPES = (
    "vault_query", "calculate", "think", "call_app",
    "chart", "write_draft", "notify",
)
# Blocks that change state the user cares about → routed through the review gate
# (interactive) or an autopilot grant (scheduled). Never silently auto-applied.
SIDE_EFFECT_TYPES = {"write_draft", "notify", "call_app"}  # call_app only if mutates=true

_FENCE_RE = re.compile(r"```eos-block[ \t]*\n(.*?)\n```", re.DOTALL)
_TEMPLATE_RE = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")
_HEADER_ORDER = ("id", "type", "output", "inputs", "side_effect",
                 "app", "method", "args", "path", "section", "shape",
                 "domain", "mutates", "continue_on_error")


@dataclass
class Block:
    id: str
    type: str
    output: str
    header: dict = field(default_factory=dict)
    body: str = ""
    inputs: dict = field(default_factory=dict)   # {local_name: upstream_output_name}
    side_effect: bool = False
    parse_error: str = ""


def _split_fence(raw: str) -> tuple[dict, str, str]:
    """Split a fence body into (header_dict, body, error). Header is the TOML
    above the first line that is exactly ``---``; body is everything after."""
    lines = raw.split("\n")
    sep = None
    for i, ln in enumerate(lines):
        if ln.strip() == "---":
            sep = i
            break
    if sep is None:
        head_src, body = raw, ""
    else:
        head_src = "\n".join(lines[:sep])
        body = "\n".join(lines[sep + 1:])
    try:
        header = tomllib.loads(head_src) if head_src.strip() else {}
    except Exception as e:  # noqa: BLE001 — fail soft, never crash the note
        return {}, body.strip("\n"), f"header TOML error: {e}"
    return header, body.strip("\n"), ""


def parse_blocks(md: str) -> list[Block]:
    """Parse every ``eos-block`` fence in document order. Prose between fences
    is ignored. Malformed fences become ``type='invalid'`` blocks carrying a
    ``parse_error`` rather than raising — the note stays hand-editable."""
    out: list[Block] = []
    for m in _FENCE_RE.finditer(md or ""):
        header, body, err = _split_fence(m.group(1))
        if err:
            out.append(Block(id=header.get("id", f"block{len(out)}"),
                             type="invalid", output="", header=header,
                             body=body, parse_error=err))
            continue
        btype = str(header.get("type", "")).strip()
        bid = str(header.get("id", "") or f"block{len(out)}").strip()
        output = str(header.get("output", "") or bid).strip()
        inputs = header.get("inputs") or {}
        if not isinstance(inputs, dict):
            inputs = {}
        side = bool(header.get("side_effect", False))
        if btype in SIDE_EFFECT_TYPES:
            if btype == "call_app":
                side = bool(header.get("mutates", False))
            else:
                side = True
        if btype not in BLOCK_TYPES:
            out.append(Block(id=bid, type="invalid", output=output,
                             header=header, body=body,
                             parse_error=f"unknown block type {btype!r}"))
            continue
        out.append(Block(id=bid, type=btype, output=output, header=header,
                         body=body, inputs=inputs, side_effect=side))
    return out


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, dict):
        inner = ", ".join(f"{k} = {_toml_value(x)}" for k, x in v.items())
        return "{ " + inner + " }"
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def _emit_header(header: dict) -> str:
    keys = [k for k in _HEADER_ORDER if k in header]
    keys += [k for k in sorted(header) if k not in _HEADER_ORDER]
    lines = []
    for k in keys:
        v = header[k]
        if isinstance(v, dict) and not v:
            continue  # drop empty inputs/args for a clean canonical form
        lines.append(f"{k} = {_toml_value(v)}")
    return "\n".join(lines)


def _block_header_dict(b: Block) -> dict:
    """Canonical header dict for a Block (header is the source of truth; ensure
    the structural keys are present so serialize is a fixed point)."""
    h = dict(b.header)
    h["id"] = b.id
    h["type"] = b.type
    h["output"] = b.output
    if b.inputs:
        h["inputs"] = b.inputs
    return h


def serialize_blocks(blocks: list[Block]) -> str:
    """Emit blocks back to canonical fenced markdown. ``serialize_blocks`` is a
    fixed point: ``serialize_blocks(parse_blocks(serialize_blocks(b))) ==
    serialize_blocks(b)``. Used by ``from-session`` to compose a fresh note;
    the engine never rewrites a user's existing fences."""
    chunks = []
    for b in blocks:
        head = _emit_header(_block_header_dict(b))
        body = b.body.strip("\n")
        chunks.append("```eos-block\n" + head + "\n---\n" + body + "\n```")
    return "\n\n".join(chunks) + ("\n" if chunks else "")


def render_template(text: str, context: dict) -> str:
    """Resolve ``{{name}}`` / ``{{outputs.name}}`` / ``{{now.date}}`` against a
    context dict. Non-string values render as compact JSON. Unresolved
    placeholders are left verbatim (so a typo is visible, not silently empty)."""
    def repl(m):
        path = m.group(1).split(".")
        node = context
        for part in path:
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return m.group(0)  # leave unresolved verbatim
        if isinstance(node, str):
            return node
        try:
            return json.dumps(node, ensure_ascii=False, default=str)
        except Exception:  # noqa: BLE001
            return str(node)
    return _TEMPLATE_RE.sub(repl, text or "")


# ── Prompts (UPPERCASE; system= kwarg; negative examples) ──────────────────
THINK_BLOCK_SYSTEM = """You are a precise analyst executing one step of a data pipeline.
You are given upstream results interpolated into the instruction below.
Rules:
- Answer ONLY what the instruction asks. No preamble, no sign-off.
- If the instruction asks for JSON, output valid JSON and nothing else.
- Ground every claim in the provided data; do not invent numbers.

Do NOT:
- Add commentary about being an AI or about the pipeline.
- Restate the instruction back.
- Output markdown code fences unless explicitly asked.
"""

FROM_SESSION_SYSTEM = """You convert an exploratory chat transcript into a Runbook:
an ordered list of typed blocks that reproduce the useful work as a re-runnable
pipeline.

Output STRICT JSON: a list of block objects. Each block object has:
  id        (short kebab-case, unique)
  type      one of: vault_query | calculate | think | chart | write_draft | notify
  output    (name later blocks reference)
  inputs    (object mapping local_name -> an earlier block's output name; {} if none)
  header    (object of extra fields for the type — e.g. tags/folder for vault_query,
             app/method/args for call_app, path/section for write_draft, shape for chart)
  body      (the prompt/query/expression text; may use {{output_name}} placeholders)

Rules:
- Use ONLY the six types above. Never invent a type.
- Every inputs reference MUST name an earlier block's output.
- calculate bodies are pure Python expressions over inputs (no statements, no imports).
- Keep it minimal — one block per genuine step.

Do NOT:
- Invent app/method names you didn't see in the transcript.
- Emit prose outside the JSON array.
- Produce write_draft/notify blocks unless the transcript clearly produced an artifact.
"""
