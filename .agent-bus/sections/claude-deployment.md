

EmptyOS deployments fall into **5 lanes**, documented in `docs/DEPLOYMENT.md`:

| # | Lane | Discriminator | Mechanism |
|---|---|---|---|
| 1 | Service | HTTP container, **no vault** | `services/<name>/` + `scripts/deploy-service.sh` |
| 2 | Daemon (single-tenant) | Full EmptyOS, **vault mounted** | `docker-compose.yml` + `scripts/redeploy-demo.sh` |
| 3 | Static site | Pre-rendered, no runtime | `eos publish deploy` |
| 4 | Bundled product | Daemon preconfigured + branded (future) | `profiles/<name>/profile.toml` |
| 5 | Multi-tenant SaaS | Daemon serving many tenants (future) | TBD |

Decision rule: **service has no vault, daemon has a vault**. Don't add vault access to a Lane 1 service — bridge through a daemon instead.

Variants compose with lanes (don't multiply them): `worker` (no vhost on Lane 1), `edge` (constrained), `air-gapped`, `hybrid` (multiple lanes per product). Out of scope: serverless, native distribution (PyPI/desktop/mobile), federation/CRDT.

**Demo vs public vs private:** `network.mode = "public"`, `demo.enabled`, and `.eos-personal` + `[app] private = true` are three orthogonal knobs — see `.claude/rules/demo-mode.md` for the contract (what each does, when to use which, and how reset/seed-on-boot are wired).

**Auth model:** the EmptyOS **daemon** is single-user by design — `auth_token` (machine) + `password` (human) are the only two credentials, applied as a network gate (not an identity system). Multi-user is **not** absent, just orthogonal: **multi-user collaboration** is the separate `commons` service (`services/emptyos-commons` — per-item owner/visibility/ACL, reached outbound, works in any `network.mode`); a **multi-tenant product** is daemon-per-user + the control-plane (`services/englishos-control-plane`) + the commons — multi-tenancy at the orchestration layer, never inside the daemon. Model: 1 vault = 1 user; N users = N vaults = N daemons; the commons is the one shared non-vault store. See `docs/AUTH.md` for the pin + the plaintext-vault rationale (a `visibility:` tag is a label, not a lock — never grow a users table inside the daemon).
