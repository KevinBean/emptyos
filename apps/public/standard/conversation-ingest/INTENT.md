# Intent - Conversation Ingest

> Living design doc for `apps/public/standard/conversation-ingest/`.
> Birth certificate: `30_Resources/EmptyOS/grill/new-app-conversation-ingest-20260725.md`.
> Changelog: `10_Projects/emptyos/log/app-development.md`.

## Why

AI conversations currently arrive through provider exports, browser sessions,
legacy Vault notes, and manually maintained queue files. The canonical Skill
defines a strong evidence-preserving process, but there is no single visual
control surface for seeing coverage, resuming the next item, inspecting
failures, or following source -> digest -> derived-note provenance.

## Relationships

**Calls into** (`self.call_app(...)`):

- (none)

**Emits:**

- (none - the first slice is read-only)

**Listens for** (`@on_event`):

- (none)

**Consumes without owning:**

- `.agents/skills/eos-ai-conversation-ingest/` - canonical workflow and contracts.
- `data/imports/` - machine queue and evidence-audit telemetry.
- Vault conversation notes - durable source, digest, derived-note, and ledger truth.

## Open questions

- Whether later executable ingestion should run through the Agent/Rooms review
  surface or become a native Pipeline after the read-only control plane proves
  the contract.
- Whether provider adapters beyond native exports should be plugins rather than
  app modules.

## Future

- Native export registration and deterministic queue rebuild.
- Review-gated audit execution with progress reporting.
- Armed user-browser capture through the existing browse provider.
- Pipeline-backed one-conversation execution after the evidence contract is
  proven in the control plane.
