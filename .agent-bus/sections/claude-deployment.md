

Five lanes, documented in `docs/DEPLOYMENT.md`: **1 Service** (HTTP container, no vault — `services/<name>/` + `scripts/deploy-service.sh`), **2 Daemon** (full EmptyOS, vault mounted — `docker-compose.yml` + `scripts/redeploy-demo.sh`), **3 Static site** (`eos publish deploy`), **4 Bundled product** (future, `profiles/<name>/profile.toml`), **5 Multi-tenant SaaS** (future). Decision rule: **service has no vault, daemon has a vault** — bridge through a daemon rather than giving a service vault access.

**Demo vs public vs private:** `network.mode = "public"`, `demo.enabled`, `[trust] web`, and `.eos-personal` + `[app] private = true` are four orthogonal knobs — `.claude/rules/demo-mode.md`.

**Auth model:** the daemon is single-user by design — `auth_token` (machine) + `password` (human), a network gate not an identity system. Multi-user collaboration is the separate `commons` service; a multi-tenant product is daemon-per-user + the control-plane + the commons. 1 vault = 1 user; never grow a users table inside the daemon (`docs/AUTH.md`).

**Operator vs user posture:** `[trust] web = "operator" | "user"` (a boot-fixed deployment fact, never a per-request role) decides whether the browser user is the machine's operator. In `user` posture (public demo, hosted learner) operator-only routes (host files, config, network, plugins, code install, generic dispatch) are refused and `read`/`write` are confined to the vault. A new core route must be classified in `emptyos/posture.py` `OPERATOR_ROUTES` or the reviewed user set (gated by `scripts/check_route_posture.py`); an app route marks itself `@web_route(..., operator=True)`. Full contract: `docs/AUTH.md` § Operator vs user + `.claude/rules/operator-posture.md`.
