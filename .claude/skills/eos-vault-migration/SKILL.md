# EmptyOS Vault Migration

Migrate one app at a time from the **legacy** vault access pattern (`vault_config()` → `Path.glob()` → manual frontmatter parse) to the **target** pattern (`VaultIndex`-backed `vault_query` / `vault_update` / `vault_get_properties` / `vault_read_section`). CLAUDE.md §Vault Data Layer names this as ongoing drift work: "Apps migrate when touched."

The only hard rule: **never silently return empty data**. If the notes aren't queryable yet (no tags, missing frontmatter fields), **add tags first via a vault script**, then refactor — never the other way around.

## When to Use

- User says "migrate `<app>`", "modernise `<app>`'s vault access", "move `<app>` to VaultIndex"
- You're already editing an app that still uses `Path.glob()` / `open()` / manual YAML parsing on vault files
- `/eos-simplify` flagged legacy vault access on a changed app

## Process

Run phases in order. **Do not refactor before Phase 3 confirms readiness** — that's the rule that keeps users from waking up to empty lists.

---

### Phase 1: Pick the App & Scan Current Pattern

```bash
# Target app path
ls apps/<id>/app.py apps/personal/<id>/app.py 2>/dev/null
```

Grep for the legacy fingerprints:

```bash
grep -n "vault_config\|vault_dir\|Path.*glob\|\.glob(\|frontmatter.parse\|yaml.safe_load\|open(" apps/<id>/app.py apps/<id>/*.py
```

Classify each hit:

| Signal | Verdict |
|---|---|
| `self.vault_config("path")` + `Path.glob("*.md")` | **legacy read** → migrate to `vault_query` |
| manual frontmatter parse (`yaml.safe_load`, split `---`) | **legacy fm** → migrate to `vault_get_properties` |
| `open(vault_path, "w")` or `Path.write_text` for a vault file | **legacy write** → migrate to `vault_update` / `vault_create_note` / `vault_append_section` |
| section-by-section parsing (`if line.startswith("## ")`) | **legacy sections** → migrate to `vault_sections` + `vault_read_section` |
| `VaultLibrary(...)` already in use | already modern — skip |

Report to user: "App `<id>` has N legacy reads, M legacy writes. Proceeding to readiness check."

---

### Phase 2: Infer the Data Contract

From the scan, figure out what the app **expects** every note to have. This becomes the reconcile contract:

- **Folder**: what `vault_config("path")` resolves to (check `_vault-map.toml`)
- **Expected tags**: if the code filters by tag (`if "song" in note.tags`) or builds a collection of one type, that tag is expected on every note
- **Expected fields**: frontmatter keys the code reads with `.get("key")` — those are the fields the migrated `vault_query(**props)` will filter on

Example for a `songs` app:
```
folder         = "30_Resources/EmptyOS/suno"   (from _vault-map.toml)
expected_tags  = ["song"]
expected_fields = ["status", "mood", "created"]
```

Write these down before the next phase — they're the arguments to `vault_reconcile`.

---

### Phase 3: Readiness Check (READ-ONLY)

Ask the daemon whether the notes are queryable *as-is*:

```bash
# Invoke reconcile via a one-off python -c or a small helper script
python -c "
import asyncio, sys
sys.path.insert(0, '.')
from emptyos.kernel import Kernel
async def main():
    k = Kernel(); await k.boot()
    app = k.apps.get('<id>')
    print(app.vault_reconcile(
        folder='<folder-from-phase-2>',
        expected_tags=<tags-from-phase-2>,
        expected_fields=<fields-from-phase-2>,
    ))
asyncio.run(main())
"
```

Reconcile returns `{"total": N, "compliant": M, "gaps": [...]}`.

Three outcomes:

| Outcome | Action |
|---|---|
| `compliant == total` and `gaps == []` | **GREEN** — go straight to Phase 5 |
| `compliant / total ≥ 0.95` and gaps are minor (one missing field) | **YELLOW** — run Phase 4 in *enrich* mode on the gap list |
| `compliant / total < 0.95` or tags missing on many notes | **RED** — Phase 4 in *bulk-tag* mode, with user confirmation |

**Never skip to Phase 5 on YELLOW/RED.** The app will return empty or partial data silently, which is the exact bug this rule exists to prevent.

---

### Phase 4: Enrich Notes (MUTATING — requires user confirmation)

This phase writes to the user's vault. **Always show the user the plan before running it.**

Present:
```
Vault migration for <id>:
  Folder       : <folder>
  Files total  : <N>
  Compliant    : <M>/<N>
  Plan         : add tag "<tag>" to <K1> notes, default field "<field>=<default>" to <K2> notes
  Backup       : git has last known state; consider committing vault before running
OK to enrich? (y/n)
```

On approval, run per-note `vault_enrich` (safe — never overwrites):

```python
for gap in reconcile_result["gaps"]:
    app.vault_enrich(
        rel_path=gap["path"],
        add_tags=[t for t in expected_tags if t not in gap.get("tags", [])],
        defaults={k: <sensible-default> for k in gap.get("missing_fields", [])},
    )
```

After enrichment, **re-run Phase 3**. Do not proceed until readiness is GREEN.

For fields where no sensible default exists (e.g. `created` timestamp, `status`), either:
- infer from file mtime / filename convention, or
- leave the field missing and adjust the migrated `vault_query` to treat absence as "unknown" — explicitly, not silently

---

### Phase 5: Refactor the App Code

Now — and only now — translate legacy patterns in `apps/<id>/app.py`:

| Legacy | Target |
|---|---|
| `for p in Path(vault_dir).glob("*.md"):` → manual parse | `for note in self.vault_query(tags=[...], folder=...):` |
| `yaml.safe_load(fm_block)` | `note["properties"]` (already parsed by VaultIndex) |
| `open(path).read()` for frontmatter only | `self.vault_get_properties(rel_path)` |
| manual section split | `self.vault_sections(rel_path)` + `self.vault_read_section(rel_path, name)` |
| `Path(path).write_text(new_fm + body)` to update a field | `self.vault_update(rel_path, {"key": value})` |
| new-note write | `self.vault_create_note(rel_path, frontmatter, body)` |
| appending to a `## Log` block | `self.vault_append_section(rel_path, "Log", text)` |

Behaviour-preservation rules:

- Keep the returned dict shape **identical** to what the app previously returned — callers (`self.call_app(...)`, API consumers, UI) must not break. If the legacy code returned `{"id": ..., "title": ..., "status": ...}`, the migrated code produces the same dict from `note["properties"]`.
- Handle missing fields with `.get("key", <sensible-default>)` — the vault is unenforced (CLAUDE.md §Vault Data Layer "Convention (not enforced)"). Never assume a key exists.
- If the app wrote *absolute* paths, convert to rel_path (relative to vault root) — VaultIndex is rel-path-keyed.
- **Do not** change public API signatures in this pass. Migration is a behind-the-scenes swap; any signature change belongs in a separate commit.

---

### Phase 6: Verify

```bash
# Static: legacy fingerprints should be gone from the app
grep -n "Path.*glob\|yaml.safe_load\|open(.*vault\|frontmatter.parse" apps/<id>/app.py
# Expected: 0 matches (or only in comments explaining why)

# Dynamic: the app's test suite must pass
pytest tests/test_sys_<id>.py -v

# Spot-check: list at least one endpoint, confirm it returns data (not [])
curl -s http://localhost:9000/<id>/api/<list-endpoint> | python -c "import sys,json; d=json.load(sys.stdin); print(f'{len(d)} items')"
```

If the count dropped from Phase 1 to Phase 6 (e.g. 42 notes before migration → 38 after), **stop**. Something in the reconcile contract is wrong — likely an expected_tag that wasn't actually on every note. Roll back the refactor, fix the contract, re-run Phase 3.

---

### Phase 7: Report

```
Vault Migration: <id>

Before:
  Legacy reads  : <N>
  Legacy writes : <M>
  Data path     : Path.glob + manual parse

Readiness (pre-enrich):
  <K>/<total> compliant — <gaps summary>
Enrichment:
  <E> notes tagged "<tag>", <F> notes got default "<field>"
Readiness (post-enrich):
  <total>/<total> compliant   ✓

After:
  vault_query / vault_update / vault_get_properties
  Items returned — before: <X>, after: <X>   ✓ (counts match)
  test_sys_<id>.py — <P> passed

Next:
  - /eos-simplify on the diff (catches any leftover legacy patterns)
  - /eos-session-wrapup to log the migration
```

## Safety

- **Read before write.** Phase 3's reconcile is read-only; never skip it.
- **Never mass-modify vault notes without user confirmation.** Phase 4 is explicit, with a visible plan.
- **Count parity is the single hardest gate.** If the migrated app returns fewer items, the migration is wrong — roll back and fix the contract.
- **`vault_enrich` is additive only.** CLAUDE.md §Vault Data Layer: "Safe — never overwrites." Don't write a replacement that overwrites — respect the contract.
- **One app per migration pass.** Don't batch multiple apps; the failure modes compound and the count-parity check becomes useless.
- **Don't change public shapes in this pass.** Migration swaps the plumbing; a schema or API change is a separate commit.
- **`apps/personal/` apps are fair game** — same rules. But `emptyos.toml` and `data/` are out of scope: this skill touches vault notes + one app file, nothing else.

## Relationship

- Pre-flight before migration on a changed app → `/eos-simplify` surfaces the legacy fingerprints
- New app that uses VaultLibrary from day one → `/eos-new-app` (skips this whole problem)
- End of session → `/eos-session-wrapup` (devlog + safety + docs-sync)
- System-wide view of migration debt → `/eos-system-check-and-fix` check mode lists apps still on legacy access
