# Soft Schema — typed contracts over a hand-editable vault

> **Vault notes remain the source of truth and stay hand-editable. EmptyOS layers
> *soft, typed contracts* over app-managed note types — never a hard schema.**

This is the durable home for the convention referenced from `CLAUDE.md` §
"Vault Data Layer → Convention (soft schema)". It reconciles a real tension and
documents the one piece of machinery that resolves it: `emptyos/sdk/vault_model.py`.

## The tension

Two true statements that look contradictory:

1. **The vault is the hard drive.** Notes are markdown + YAML frontmatter, mounted
   externally, hand-edited in Obsidian, and decades-tolerant. A human can open any
   note and change any field. Nothing may force a note into a rigid shape, or the
   "vault is external, swappable, human-readable" promise breaks.
2. **Apps need predictable shapes.** `match_score` should be an `int`, `active` a
   `bool`, `created` a `date`. YAML hands everything back as strings (`"30"`,
   `"true"`), legacy notes carry old field names (`Application_Sent` →
   `status`), and missing fields are normal. Without normalization, every read
   site reinvents the same defensive coercion — and they drift.

The old docs resolved this by *denying* (2): *"No schema enforcement — the vault
notes ARE the schema."* But the codebase had already resolved it correctly via a
**soft** contract. The docs just never caught up. This file is that update.

## The answer: `VaultModel`

A `VaultModel` is a Pydantic view over one note type's frontmatter. It is **soft**
in four deliberate ways, each mapping to a design rule:

| Mechanism | Pydantic config | What it buys |
|---|---|---|
| **Round-trip unknowns** | `extra="allow"` | A human (or a future app) adds a field the model doesn't know; it survives a read→write cycle untouched. No schema bump forced on legacy notes. |
| **Coerce at the boundary** | `mode="before"` validators | YAML-string reality is absorbed once: `"30" → 30`, `"true" → True`, `"2026-06-04" → date`. Centralized, not re-implemented per call site. |
| **Map legacy keys** | `_legacy_aliases()` override | Old field names (`Application_Sent`, `trust-level`) fold into the modern shape during validation. |
| **Validate on write, fail soft on read** | `validate_assignment=True` + try/except in `from_query_row` | A bad value is rejected *on write*; a single corrupt legacy note logs + returns `None` *on read* instead of crashing the whole list. |

The contract **is** the `VaultModel` subclass — a few lines of typed fields — not a
prose schema doc that drifts. If a human-readable contract is ever needed, generate
it from the model (the `eos app info` pattern), don't hand-maintain it.

### Shape

```python
from emptyos.sdk import VaultModel, int_or_none_validator

class JobApplication(VaultModel):
    TAG = "job-application"          # tag-scoped query key
    FOLDER = ""                      # optional default folder

    company: str
    role: str = ""
    status: str = "applied"
    match_score: int | None = None
    created: date | None = None

    _coerce_match_score = int_or_none_validator("match_score")

    @classmethod
    def _legacy_aliases(cls, raw: dict) -> dict:
        if not raw.get("status"):
            if str(raw.get("Application_Sent", "")).lower() == "true":
                raw["status"] = "interview"
        return raw

    @classmethod
    def settable_fields(cls) -> set[str]:
        return {"status", "salary", "match_score", "priority"}
```

```python
apps = JobApplication.read_all(self)               # tag-scoped query + parse (fail-soft)
one  = JobApplication.read(self, path)             # single note
JobApplication.update(self, path, status="offer")  # validates before write
```

> **Read `str`, not `Literal[...]`, for enum-ish fields.** A `Literal` rejects any
> value the model doesn't enumerate, which turns a hand-typed status into a dropped
> row on read. Keep the field a `str` and validate ranges in `audit()` if needed —
> tolerance is the point.

## `VaultModel` vs `VaultLibrary` — they're different layers

Both touch the vault; they are not alternatives.

| | `VaultLibrary` | `VaultModel` |
|---|---|---|
| **Unit** | A *collection* of notes of one type | The *typed contract* for one note |
| **Shape** | Returns plain `dict`s | Returns typed instances |
| **Methods** | `list`, `search`, `stats`, `find_by`, `count` | `read_all`, `read`, `update`, `settable_fields`, `to_frontmatter` |
| **Use when** | You want query/aggregate/stats over a folder of items | You want one note's fields coerced + validated |

An app can use both: `VaultLibrary` for the list view, `VaultModel` for the per-note
write contract. They share the underlying `vault_query` / `vault_update` plumbing.

## The graduation discipline (CLAUDE.md rule 9)

`VaultModel` was extracted to the SDK after **one** real consumer
(`apps/personal/jobs`, gitignored) proved the shape. It deliberately stayed
*unexported* from `emptyos/sdk/__init__.py` while it had a single consumer — the
public surface waits for the second.

The **second consumer** (`apps/public/standard/people`, in-tree, public) is what
promotes it: only then does it earn the package export and become load-bearing
platform infrastructure rather than one app's helper. This is the rule-9 cadence —
build specific first, extract on the second consumer — applied to the data layer.

Adopting `people` also fixes a concrete latent bug: its boards `set_field` wrote
values straight to disk **without** the `bool()`/`float()` coercion its read paths
applied, so a board edit of `active=0` (a falsy int) bypassed normalization. Routing
`set_field` through the model's `validate_assignment` + `mode="before"` coercers
closes that gap at the write boundary.

## What is deliberately *not* built (deferred until a real trigger)

- **Envelope `schema_version`.** `extra="allow"` already round-trips unknown fields,
  which is ~80% of migration safety. A `schema_version` field + an idempotent
  migration is added *only the first time a specific type breaks compatibility* —
  never pre-emptively, never in the universal envelope.
- **A migration framework.** Waits for a *second* type to need the same plumbing
  (rule 9). One type that needs a migration writes its own; the framework is earned,
  not assumed.
- **Per-type prose contract docs.** The model is the contract; generate docs if
  needed, don't hand-write drift.

## Audit, not enforce

Validation is **report-first**. `VaultModel.audit(app)` surfaces rejected/invalid
rows (missing primary tag, bad enums/dates, broken refs) and reports by default;
repairs only on explicit approval — mirroring `scripts/check_vault_test_leak.py`
(report-default, exit-code = count needing human review). The vault is never
silently rewritten to satisfy a model.

## Cross-references

- `emptyos/sdk/vault_model.py` — the implementation + coercion helpers.
- `tests/test_sdk_vault_model.py` — the contract's test suite.
- `CLAUDE.md` § Vault Data Layer — the three-layer note structure + the convention
  this doc expands.
- `.claude/rules/boards-as-view-layer.md` — the `set_field` / `SETTABLE_FIELDS`
  contract `settable_fields()` feeds.
- `.claude/rules/authorship-boundary.md` — who *wrote* a note (orthogonal axis).
