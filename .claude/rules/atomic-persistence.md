---
abstract: Use one crash-safe replacement primitive for whole-file persistence; keep logical read-modify-write locks separate.
---

# Atomic persistence

Whole-file stores must not expose a truncated or half-written document after a
process or machine interruption. Use `emptyos.runtime.atomic_io` (publicly
re-exported from `emptyos.sdk`) rather than hand-rolling `*.tmp` + rename.

## Lineage

Absorbed from `paviro/Notema` at commit
`368016a11a91f0803ce759e98b326c780e0bc732`. Notema routes every persisted TOML
or entry document through `notema_encryption::atomic_write`: sibling temp,
file fsync, replace, then parent-directory fsync. EmptyOS already had the same
core shape privately in `emptyos/web/routes_vault.py`; this rule graduates that
working implementation into one shared primitive rather than porting Notema's
Rust code.

## Contract

`atomic_write_bytes(path, content)` and `atomic_write_text(path, content)`:

- create the temporary file beside the destination, so replacement stays on
  one filesystem;
- flush and fsync the complete temporary payload before `os.replace`;
- retain an existing target's permission bits; a brand-new file defaults to
  owner-only (`0o600`) rather than the process umask — decided 2026-08-02,
  matching the codebase's one other explicit permission policy
  (`emptyos/sdk/autopilot.py`'s secrets key file) since nothing needs a
  freshly atomic-written file to be group/world-readable, and this is the
  primitive vault content (journal notes today) goes through;
- best-effort fsync the parent directory where the platform supports it;
- remove a stranded temp when writing or replacement fails.

Use `await self.write(path, content, atomic=True)` from apps so the filesystem
provider moves the blocking fsync work off the event loop.

## What it does not solve

Atomic replacement prevents a torn file. It does **not** prevent two writers
from reading the same old value and replacing each other. Any read -> mutate ->
write sequence still needs `async with self.note_lock(path)` for vault notes or
`self.write_lock(key)` for other app state. Keep emits outside that lock.

It also does not make append streams transactional and is not a reason to
rewrite large media files through memory. Large or streaming artifacts keep
their domain-specific staging path.

## Migration discipline

The journal is the first new consumer, dark behind
`[apps.journal] feature.atomic-note-writes.enabled = true`; the existing raw
vault HTTP writer is the already-live seed consumer. Default-off journal calls
remain byte-for-byte on the legacy provider path.

Verified same-shape follow-on consumers (migrate when next touched, not in one
big-bang patch):

- `emptyos/runtime/vault_index.py` — create, frontmatter update, section append;
- `emptyos/sdk/vault_library.py` — frontmatter and body replacement;
- JSON/TOML stores that currently maintain their own sibling-temp helpers.

Do not create a second helper per app. If a store needs different semantics
(compare-and-swap, append, encryption, streaming), name that difference rather
than stretching this replacement-write primitive.
