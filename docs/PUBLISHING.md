# Publishing & Release Workflows

EmptyOS publishes in **three families**. This is the single map of every
public surface → the workflow that produces it. (Deployment *lanes* — service
vs daemon vs static — are in `docs/DEPLOYMENT.md`; this doc is the *publish
mechanism* on top of them.)

| Family | Source | Transform | Target | Entry point |
|---|---|---|---|---|
| **1. Daemon release** | a `release.toml` **tier** | snapshot + tier-filter + gates | a git repo → VPS rebuild | `python scripts/release.py <target>` |
| **2. Static site** | a vault **folder** (or repo path) | publish-app build | gh-pages repo → GitHub Pages | `eos publish deploy <site>` |
| **3. Service** | a `services/` **dir** | docker build | VPS container | `scripts/deploy-service.sh <name>` |

## Family 1 — daemon releases (`scripts/release.py`)

Targets are declared in `release.toml` `[targets.<name>]` (tier + repo +
gates + transform). One engine drives them all:

- `release.py public` — **delegates** to the proven `scripts/release-public.py`
  (the OSS flow: the PUBLIC_TIERS — core, standard, englishos — personal+branding strip, public repo).
  Unchanged behaviour; `release.py` is just the unified entry.
- `release.py portfolio` — private branded release: worktree snapshot (captures
  untracked/WIP), `portfolio` tier (standard + engineering apps + bess-analyser
  + welcome), branding gate only (personal content is intentional in a private
  repo), bundles `portfolio/` (config + vault) + a standalone `docker-compose.yml`,
  pushes to the **private** `KevinBean/emptyos-portfolio`.
- `release.py plekto` — branded commercial distribution (tier `plekto`).

`--dry-run` builds the snapshot and prints a summary without pushing; a real
push prompts unless `--yes`.

## Family 2 — static sites (`eos publish deploy <site>`)

Site profiles live in `data/apps/publish/sites.json`; the publish app builds
markdown → HTML and force-pushes `gh-pages` (with a CNAME) to each site's repo.
See the publish app UI or `eos publish {sites,build,deploy} --site <id>`.

Each site may carry a **voice note** at `{vault}/<source_folder>/_voice.md` —
the writer's AI editorial actions (polish/review/chat/…/adapt_linkedin) append
it to their system prompts so drafts follow that site's voice. Builder-invisible
(`_` prefix), presence-gated (no note = no behavior change). See
`docs/BRAND-OPS.md` § Voice discipline.

## Family 3 — services (`scripts/deploy-service.sh`)

Lane-1 HTTP containers with **no vault** (e.g. the chatbot). See
`docs/DEPLOYMENT.md` § Service.

## The complete domain map

| Domain | Family | Command | Repo / host | Notes |
|---|---|---|---|---|
| **demo.binbian.net** | 1 daemon | `release.py public` → VPS `redeploy-demo.sh` | `KevinBean/emptyos` (public) | generic product demo |
| **os.binbian.net** | 1 daemon | `release.py portfolio` → VPS `git pull` + `docker compose up --build` | `KevinBean/emptyos-portfolio` (**private**) | live engineering portfolio; OpenAI-only |
| **binbian.net** | 2 static | `eos publish deploy default` | `KevinBean/KevinBean.github.io` | personal blog |
| **portfolio.binbian.net** | 2 static | `eos publish deploy portfolio` | `KevinBean/portfolio-site` | conventional CV front door; links → os.binbian.net |
| **eos.binbian.net** | 2 static | `eos publish deploy emptyos` | `KevinBean/emptyos-site` | EmptyOS landing/docs |
| **plekto.dev** | 2 static-mirror | `eos publish deploy plekto` | `plekto-dev/site` | branded site |
| **chat.binbian.net** | 3 service | `deploy-service.sh chatbot` | VPS container | chatbot backend for the static sites |

## Adding a new daemon release target

1. Define (or reuse) a tier in `release.toml` `[tiers.<name>]`.
2. Add `[targets.<name>]` binding the tier → repo + gates + transform.
3. `python scripts/release.py <name> --dry-run` to inspect, then without
   `--dry-run` to push.
4. Document the domain row above + wire the VPS (clone/pull + compose + Caddy).

## Safety gates (family 1)

- `strip_personal = true` → `scripts/check-personal.py` runs against the working
  tree; **any** match aborts the release. Public targets only — private targets
  intentionally carry the owner's name/content.
- `strip_branding = true` → `scripts/check-branding.py` likewise.
- Snapshots never include `data/`, root `emptyos.toml`, `apps/personal/`,
  `.env*`, or caches (see `_IGNORE` / `_PRUNE_PATHS` in `scripts/release.py`).
