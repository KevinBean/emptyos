---
paths:
  - "products/**"
  - "scripts/build_standalone_release.py"
---
# Product Packaging Rule — slicing EmptyOS apps into distributable products

EmptyOS is the **foundry**: apps are grown in conversation mode, on the daemon,
with the vault. A **product** is a frozen vertical slice stamped out of it for
someone who will never run the daemon — one exe (or one URL), no clone, no pip,
no toml. Products are a *distribution format*, not a deployment lane: they have
no conversation mode, no event bus, no store. The daemon stays the place where
things evolve.

**Reference implementation:** `products/writedesk/` (写作台 — writing-editor
Documents mode + jianpu composer, built 2026-06-10). Read its `README.md`
before building product #2.

## The pattern (proven by WriteDesk)

```
apps/*/pages/        ──copy + patch (asserted markers)──►  assets/   (no fork)
apps/*/app.py        ──ast-extract constants──►            prompts.json
@web_route surface   ──hand-written mimic──►               server.py (slim FastAPI)
capabilities         ──collapse──►                         direct cloud REST (key in config.toml)
vault                ──collapse──►                         plain .md + frontmatter in Documents/
launcher.py          window lifecycle: browser --app + dedicated profile,
                     lifetime by PAGE HEARTBEAT (/api/ping every 3s), never
                     by the spawned process handle (Edge re-execs/hands off)
build.py             assemble assets → PyInstaller onefile
```

Hard-won launcher rules (don't rediscover): `--noconsole` exes have
`sys.stdout/stderr = None` (give uvicorn devnull streams + `log_config=None`);
pre-create the browser profile dir; always write a startup line to a log file
next to the exe; ship the API key in `config.toml` beside the exe, never baked
into the binary.

## Which apps slice cleanly

| App depends on | Packageable? |
|---|---|
| Own pages + pure logic | ✅ |
| `engines/` (pure, kernel-free by design) | ✅ best case — fully offline exe |
| Cloud REST think/listen via API key | ✅ (config.toml key + network) |
| Plain file storage | ✅ |
| `call_app` chains / event-bus ripples | ⚠️ only by bundling the whole chain |
| Local GPU services, vault index, store | ❌ daemon-only |

Product data files stay **vault-compatible markdown** (block-style tags) so a
product's output can migrate up into a real vault later. One-way door, the
right direction.

## The two tiers (decided 2026-06-10)

| Tier | For | Mechanism | Protection reality |
|---|---|---|---|
| **Open** | Simple tools, family/free products, no IP concern | PyInstaller onefile, as WriteDesk | Bytecode is decompilable; deters casual copying only. Fine — nothing secret inside. |
| **Secret** | Anything with real IP (calibrated engines, tuned prompt chains, sellable products) | **Thin client + Lane 1 service**: the valuable logic deploys as a `services/<name>/` HTTP service (see `docs/DEPLOYMENT.md` Lane 1 + `scripts/deploy-service.sh`); the client is a license-keyed thin exe or just a web page | The secret never ships. The only protection that actually holds. |

No middle tier by default. If a *standalone-but-protected* exe is ever truly
required (offline site laptop + real IP), the escape hatch is swapping
PyInstaller for **Nuitka** (Python → C → machine code; no bytecode to
decompile), optionally + PyArmor — add a `--protect` build flag then, not
before. Client-side code is never cryptographically secure; don't pretend
otherwise in any product README.

**Below the exe tier** sits the browser-delivered pair — single-html file +
MV3 Chrome extension for export-enabled apps
(`.claude/rules/standalone-distribution.md`,
`python scripts/build_standalone_release.py <app_id>`). Reach for it when the
target IT environment disallows even an exe; it trades the exe's
filesystem/GPU access for zero-install browser delivery. Open-tier only —
the bundle ships all its JS.

## Platforms (decided 2026-06-10)

- **Windows** — primary target, build on this machine (current state).
- **macOS / Linux** — PyInstaller does NOT cross-compile; build on the target
  OS via a **GitHub Actions matrix** (windows/macos/ubuntu runners) when the
  first non-Windows product is wanted. Known per-OS work, deferred until then:
  - launcher `find_browser()` is Windows-pathed — needs `sys.platform`
    branches (mac/linux: Chrome/Chromium `--app` works; Safari can't).
  - macOS Gatekeeper blocks unsigned apps — either document right-click-open
    or pay for Apple Developer signing/notarization. Decide per product.
  - Linux users are usually technical — "run from source" or an AppImage may
    beat an exe; don't assume the Windows shape.
- **Secret tier is cross-platform for free** — the service runs server-side;
  the thinnest client is the browser itself.

## The shared pipeline (`products/_shared/`) — built 2026-07-15 at product #2

The graduation fired. A product is now **a `product.toml`**, not a codebase:

```toml
[product]
id = "emptyos-desktop"
tier = "standard"            # any release.toml tier — this is what makes it tier-agnostic
exe_name = "EmptyOS"
start_url = "/hub/"
welcome_url = "/settings/pages/welcome.html"
appdata_name = "EmptyOS"
brand_dir = "brand/emptyos"
[update]
feed = "https://github.com/KevinBean/emptyos/releases/latest/download/latest-emptyos-desktop.json"
```

| Piece | Does |
|---|---|
| `launcher_core.py` | first-run config, daemon-as-subprocess, restart loop, window, log |
| `tray.py` | tray in the **launcher** process, so it survives daemon restarts |
| `updater.py` | versioned installs, checksummed download, `.ok` marker, prune |
| `stub.py` / `stub.spec` | the exe the shortcut points at; picks the newest complete version |
| `product.spec` | generic PyInstaller spec (any tier, any brand) |
| `build_release.py` | zip + `latest.json` + `.sha256` |
| `smoke.py` / `smoke_update.py` | boot the real artifact; drive a real update |

`products/desktop-windows/` is the reference (full daemon, `standard` tier);
`products/desktop-macos/` is its second consumer (runs from source, native
webview). Reviving **Plekto** is now a `product.toml`, not a project.

## Hard-won: a product only exists once you have run the artifact

Every bug in the 2026-07-15 build was invisible to unit tests, `node --check`,
and a green `pytest` — because none of them exist until the code is *frozen*:

- **numpy/scipy extension DLLs deadlock** under the frozen loader lock once the
  process has threads. The daemon hung forever inside `import numpy`, printing
  nothing, exiting nothing. Only a py-spy dump of the wedged process showed it.
  Fix: pin the BLAS thread pools (`OPENBLAS_NUM_THREADS=1`) *and* warm-import the
  native stack single-threaded before the kernel starts (`WARM_IMPORTS`).
- **`collect_submodules("emptyos")` → a 5.73 GB bundle** (torch 3.8 GB): it
  imports *every* module, including optional ones, dragging their deps behind
  them. Never collect the app package; it ships as source in the tier tree.
- **stdin EOF is not a shutdown signal.** EOF is the normal state of a process
  with no stdin, so a daemon that treats it as "stop" kills itself at boot.
- **A PyInstaller `excludes` entry can break an engine** (`unittest` — inherited
  from plekto.spec, where those engines weren't in the tier so it never showed).

So: `smoke.py` runs in CI before any release. A build that boots is the only
evidence that matters.

## Graduation triggers (rule 9 discipline)

- **Product #3 with the same mini-server shape** → consider generating the
  server from the apps' `@web_route` surface (converges with
  `[provides.export]` machinery). Hand-written until then — two data points
  aren't a pattern.
- **First sellable product** → that's when the Secret tier's licensing story
  (keys, expiry, machine binding) gets designed. Not speculatively. The *seam*
  exists today: a `product.license_key` setting the About panel writes, which a
  secret verb would send to a Lane-1 service as a bearer token.
- **Signing** → unsigned today; every release ships a `.sha256` and the docs tell
  the user to check it. Azure Trusted Signing (~$10/mo, CI-friendly) when
  distribution outgrows "verify the checksum".

## When NOT to make a product

- The user can run the daemon — give them the daemon; products lose the bus,
  the vault, the growth loop.
- The app's value IS its connections (reactor ripples, cross-app chains) — a
  slice would be a hollow shell.
- "Just in case someone wants it" — a product needs a named human who will
  actually double-click it. WriteDesk had one. Keep that bar.
