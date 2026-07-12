# Agent-Facing CLI Convention — structured output for new `eos` commands

Borrowed from `cursor/plugins`' "CLI for agents" idea. EmptyOS's `eos` CLI is
human-first (Rich tables, coloured errors) and stays that way. But a command that
an **agent** will parse — an LLM-driven session, a `/loop` job, a CI gate — needs a
machine-readable mode. This rule is the **forward convention** for that mode.

**This is not a retrofit mandate.** Existing commands keep their human output. New
agent-facing commands (and old ones *when touched for agent use*) add a `--json` flag
following the envelope below. The first command built to this convention is `eos verb`
(`emptyos/cli/commands/verb.py`); the precedent for exit-code-as-signal is
`eos bus ripple --dry-run` (`emptyos/cli/commands/bus.py`).

## The envelope

Under `--json`, a command prints **exactly one JSON object** to stdout and nothing else:

```json
{"ok": true, "code": "ok", "message": "3 verbs", "data": {"verbs": [...]}}
```

| Field | Type | Meaning |
|---|---|---|
| `ok` | bool | Success. Mirrors the exit code: `ok:true` ⇔ exit 0. |
| `code` | str | Short machine token: `ok`, `not_found`, `drift`, `invalid_args`, `no_daemon`, `error`. |
| `message` | str | One human-readable line. Never multi-line, never decorative. |
| `data` | any | The payload. Omitted or `null` when there's nothing to return. |

Rules:
- **stdout carries only the JSON** in `--json` mode — no Rich tables, no log lines, no
  progress text. Diagnostics (if any) go to stderr so a parser can `json.loads(stdout)`.
- **Exit code is the primary signal**: `0` on `ok:true`, non-zero on `ok:false`. A
  pure check command (drift / dry-run) uses the exit code as its answer, like
  `eos bus ripple --dry-run` (exit 1 = changes pending). Agents branch on the code; the
  JSON explains.
- **Human mode is the default.** No flag → Rich/plain human output, unchanged. `--json`
  is purely additive.
- **`--dry-run` where the command mutates.** Report what *would* change, write nothing,
  and signal via exit code + `code`.

## Minimal shape

```python
from emptyos.cli._common import emit

emit(
    ok,
    "ok" if ok else "error",
    message,
    data,
    as_json=as_json,
    human=render_human,
)
```

Use the shared ``emit()`` helper so envelope shape and exit semantics cannot drift
between command modules.

Standalone scanners under ``scripts/`` cannot import the CLI package, so they have
their own one-liner: ``from scanner_lib import emit_json`` (``scripts/scanner_lib.py``).
Same envelope, same exit semantics. Adopt it when you touch a scanner's ``--json``
branch — nineteen of them hand-rolled ``json.dumps({...})`` and the shape had already
drifted (``check_call_app_declared.py`` emits no ``code``/``message`` and spreads its
payload across the top level).

## When NOT to add `--json`

- The command is interactive (a REPL: `eos rooms`, `eos chat`) — there's no single
  result object to emit.
- The command is purely human chrome (status dashboards a person reads). Add `--json`
  only when an agent or script actually consumes it.
- You'd be retrofitting an existing stable command with no agent consumer asking for it.
  Wait for the consumer (same restraint as CLAUDE.md rule 9).

## Cross-references

- `emptyos/cli/commands/verb.py` — first command built to this convention.
- `emptyos/cli/_common.py` — shared envelope, config lookup, auth, and health plumbing.
- `emptyos/cli/commands/bus.py` — exit-code-as-signal precedent (`ripple --dry-run`).
- `emptyos/sdk/verb_registry.py` — the registry `eos verb` reads.
- `docs/OPEN-SOURCE-BORROWING-PLAN.md` — why this exists (cursor/plugins borrow).
