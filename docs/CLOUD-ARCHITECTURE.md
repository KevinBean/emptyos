# Cloud Architecture — storage domains + the migration posture

> **The migration principle: never migrate the vault; migrate the cloud layers
> around it.** The vault stays plain markdown, mounted, human-readable, owned by
> one human (`docs/AUTH.md`). Cloud capability comes from the *services beside*
> the daemon — commons (shared records), codoc (live collaboration), the control
> plane (tenancy/orchestration) — never from growing multi-user state inside the
> daemon. This document is the Phase-0 ADR: it names the storage domains, maps
> what already implements each, and sequences the remaining work.

Status legend for the "today" columns: **built** (in the repo, tested),
**built-dark** (behind a flag / not deployed), **live** (running in a real
deployment), **deferred** (registered in `docs/DEFERRED-WORK.md` with a trigger).

## The six storage domains

Everything the system persists falls into exactly one of these domains. The
domain decides the owner, the source-of-truth rule, and the backup posture.

| Domain | What lives there | Owner / where | Source-of-truth rule | Backup posture |
|---|---|---|---|---|
| **vault** | Human-authored + recovery-critical knowledge (notes, journal, projects, KB) | The user; external `notes.path`, markdown + frontmatter | THE source of truth for user content. Never mirrored into a DB as primary | Whole-directory copy / sync; cloud learners: AES-GCM archive via `services/englishos-control-plane/englishos_control_plane/lifecycle.py` (covers vault + data volumes per learner) |
| **data** | Daemon-local operational state (event history, syslog, billing counters, chat sessions, store state) | The daemon; `data/` (SQLite/JSON), per machine | Authoritative for machine telemetry only; never holds user knowledge (CLAUDE.md § Storage & Vault) | Same lifecycle archive on cloud learners; local machines: expendable-ish (regenerable except audit trails) |
| **index** | Rebuildable query state derived from the vault (VaultIndex, embeddings) | The daemon; in memory by design — `emptyos/runtime/vault_index.py`: "plain Python dicts in memory. No SQLite, no files. Rescan on restart" | Derived — the vault is truth; any index can be dropped and rebuilt (rescan ≈800ms for 3000+ files, per `docs/DESIGN.md` § VaultIndex) | None needed. A *persistent* per-daemon index is deferred with a trigger (see Phase 6) |
| **commons** | Published/shared records with owner/visibility/ACL — the one shared non-vault store | `services/emptyos-commons` (Lane 1); SQLite or Postgres (`COMMONS_DATABASE_URL`), RLS backstop on Postgres | Authoritative for *shared* copies + ACLs. The vault note keeps a `commons:` frontmatter LABEL only — "NOT the source of truth; the commons DB is" (`apps/public/standard/commons/app.py::_write_published_hint`) | `commons-data` volume backup (documented in `services/emptyos-commons/README.md`) |
| **live** | CRDT collaboration state (co-editing sessions, presence) | `services/emptyos-codoc` (Lane 1); pycrdt server-side merge, ACL-gated WebSocket, `X-EOS-Principal` trust model | Ephemeral-authoritative during a session; durable outcome lands in the **vault** via the snapshot write-side (`POST /codoc/api/save` → `author: both` note) | None beyond the vault snapshot — the doc state is reconstructible from the saved note |
| **control** | Tenancy + orchestration state (users, instance routing, plans, sessions) | `services/englishos-control-plane`; Postgres (`englishos_users`, `englishos_learner_instances`) | Authoritative for who-has-which-daemon; never visible to app code — apps have no `control` concept (see the manifest vocabulary below) | Standard DB backup of the control-plane Postgres; per-learner volume archives via `lifecycle.py` |

### Relationship to DESIGN.md "Two Storage Domains"

`docs/DESIGN.md` § Two Storage Domains defines the **local-daemon view**: vault
(user state) + kernel storage (`data/`). That table remains correct and
unchanged for a single machine. This document is the **deployment-wide
superset**: `index` is split out of kernel storage because its
derived/rebuildable nature carries different rules (no backup, free to
persist-or-not), and `commons` / `live` / `control` only exist once services
run beside the daemon. A laptop with no services uses exactly the DESIGN.md
two; a cloud deployment uses all six.

## `[storage]` manifest convention (docs-only, non-enforcing)

An app MAY declare which domains it touches. Values are **domain names** from
the table above; `control` is excluded (it is never app-addressable — only the
control plane itself touches it). Absence of the section asserts nothing — all
existing apps keep working undeclared.

```toml
[storage]
private = "vault"      # human-owned notes
operational = "data"   # daemon-local state
shared = "commons"     # published/shared records
live = "live"          # CRDT collaboration state
derived = "index"      # rebuildable query state
```

Only declare the domains the app actually touches (most apps declare one or
two). The advisory scanner `scripts/check_storage_decl.py` (registered in
`scripts/preflight.py`, scope `apps`, non-gating) reports adoption count and
flags invalid declarations (unknown domain, `control`, non-string values). See
also the manifest reference in `docs/APP-DEVELOPMENT.md` § Manifest.

## Current-state map (honest status, 2026-07-15)

| Layer | Implements | Status + gaps |
|---|---|---|
| Commons service | `services/emptyos-commons` — notes, ACL, visibility, API tokens; SQLite/Postgres; RLS backstop | **Built.** Full management surface (share, visibility change, revoke) lives on the service |
| Commons daemon bridge | `apps/public/standard/commons/app.py` — exactly `api_publish`, `api_revoke`, `api_feed`, `api_suggest` | **Built, deliberately thin.** Gap (Phase 4): no post-publish share management, visibility change, body update, or remote-ACL view from the daemon side |
| Codoc service | `services/emptyos-codoc` — pycrdt server merge, ACL-gated WS, `X-EOS-Principal` injected by the trusted control-plane hop | **Built + browser-verified.** Lane-1 artifacts (Dockerfile/compose/service.toml) built, unrun |
| Codoc daemon app | `apps/public/standard/codoc/` — `POST /codoc/api/save` (snapshot → `author: both` vault note), `POST /codoc/api/agent-edit` (agent joins the CRDT session) | **Built + live-verified.** Snapshot write-side is installed on `:9000`; the editor save button is wired but browser-unverified after that wiring. Remaining work is deployment/routing, not another bridge implementation |
| Control plane | `services/englishos-control-plane` — auth, provisioning, proxy, plans (`plans.py::plan_apps`), lifecycle backup | **Built + product-proven, EnglishOS-named** (`ENGLISHOS_*` env, `englishos_*` tables). Its README's "One mechanism, many products" already covers rebranding with zero code change |
| Derived index | `emptyos/runtime/vault_index.py` — in-memory, rescan on boot | **By design.** Persistence is trigger-gated (Phase 6), not a gap |
| Backup | `lifecycle.py` AES-GCM per-learner vault+data archive (keygen/backup/restore/export/delete via `learner_lifecycle.py`); commons `commons-data` volume | **Built.** Not greenfield — Phase 8 extends, not creates |

## Roadmap (corrected)

Phases are ordered by value/risk, not calendar. Several are trigger-gated
registry rows rather than scheduled work — that is deliberate
(`docs/DEFERRED-WORK.md` discipline).

- **Phase 0 — this document.** Domain vocabulary + current-state map + the
  `[storage]` convention + advisory scanner. No behavior change.
- **Phase 1 — `[storage]` adoption on touch.** Apps declare domains when
  touched (same migrate-when-touched discipline as VaultIndex adoption,
  CLAUDE.md § Vault Data Layer). The scanner stays advisory; it never gates.
- **Phase 2 — control-plane generalization, compat-mode only.** `EOS_CONTROL_*`
  env aliases reading through to the existing `ENGLISHOS_*` names. **Table
  renames deferred** — Postgres renames/views add operational risk for zero
  behavior change; revisit only when the SaaS trigger fires.
- **Phase 3 — stateless-product front door: trigger-gated, not scheduled.**
  Fully specified in `docs/DEFERRED-WORK.md` — the row "SaaS front door for
  **stateless** products (`product_type` discriminator + `StatelessProvisioner`
  …)", trigger = a second paying buyer / self-serve demand, Phase-1 reference
  `services/earthing-calc/` (built). Do not re-sequence it here.
- **Phase 4 — commons bridge parity.** Add the missing daemon-side management
  verbs (share list/update, visibility change, remote-ACL view) as thin calls
  to the service's existing endpoints. The frontmatter-label posture is
  unchanged: the commons DB stays the source of truth.
- **Phase 5 — codoc deploy + route.** Re-scoped from "wire it" (the daemon bridge
  is installed and live-verified) to the remaining operator path: add the
  control-plane routing key, run the real compose build/deploy, and browser-verify
  the already-wired save button through that deployed route. The trigger remains
  the first real multi-user deployment wanting live co-edit.
- **Phase 6 — persistent derived index: trigger-gated.** Registered in
  `docs/DEFERRED-WORK.md` (the FTS5/BM25 lexical-index row, extended with the
  per-daemon persistence angle). Trigger = a felt recall gap or measured
  rescan/search latency — not a phase slot. Near-term cloud FTS need lands in
  the **commons** (Postgres FTS), not per-daemon.
- **Phase 7 — migrate-when-touched.** Already standing doctrine (CLAUDE.md
  § Vault Data Layer + the `eos-vault-migration` skill). Nothing new to build.
- **Phase 8 — backup completion.** Extend the existing implementations
  (lifecycle AES-GCM archives, commons volume) to any deployment shape that
  lacks one; do not rebuild them.

## Non-Goals (standing pins, restated)

These are existing decisions this migration must not erode — each is pinned
elsewhere; this list is a cross-reference, not new policy.

- **No vault-to-database migration, ever.** The vault is markdown files; every
  index over it is derived and rebuildable. (CLAUDE.md § Storage & Vault)
- **No users table / multi-tenancy inside the daemon.** 1 vault = 1 user =
  1 daemon; N users = N daemons + control plane + commons. (`docs/AUTH.md` —
  "What we will refuse to add")
- **No frontmatter-as-ACL.** A `visibility:` tag is a label, not a lock; ACLs
  live in the commons DB. (`docs/AUTH.md`, commons bridge design)
- **No cloud requirement for local operation.** Every service is additive; a
  laptop with zero services is a complete EmptyOS. (CLAUDE.md Rule 17/20)
- **No per-request user identity in app code.** The only auth-presence branch
  apps may take is the public-app pattern
  (`.claude/rules/public-app-pattern.md`).
