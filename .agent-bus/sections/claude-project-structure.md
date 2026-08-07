

```
D:\emptyos\
├── emptyos/
│   ├── kernel/             # Config, EventBus, ServiceRegistry, loaders
│   ├── capabilities/       # 16 capabilities + providers
│   ├── sdk/                # BaseApp, BasePlugin, decorators, VaultLibrary, srs, utils
│   ├── cli/                # eos command (Typer) — daemon client mode
│   ├── web/                # FastAPI server + auto-UI + topology
│   │   └── static/         # theme.css, eos.js, eos-components.*, eos-keys.*
│   └── runtime/            # vault watcher, vault_index, scheduler, realtime, vault_map
├── apps/                   # App track tree (see below)
│   ├── public/             #   core/ standard/ labs/ — git-tracked, OSS
│   ├── extension/          #   engineering/ … dev/ others/ labs/ — tracked, never public
│   └── personal/           #   user apps + labs/ (gitignored)
├── plugins/                # auto-discovered, loaded before apps
├── products/               # double-clickable builds: a slice of apps (writedesk) OR the whole
│                           #   daemon (desktop-windows). One product = one product.toml naming a
│                           #   release.toml tier; shared pipeline in products/_shared/.
│                           #   See .claude/rules/product-packaging.md + docs/DESKTOP.md
├── engines/personal/       # User engines (gitignored)
├── data/                   # Runtime state
├── emptyos.toml            # Machine config (.gitignored)
└── restart.bat             # Kill + boot
```

Loaders scan the whole `apps/` track tree (any depth) via `emptyos/sdk/app_layout.py` (`iter_app_dirs`) — all apps equal at runtime; **app ids are independent of folder location**. A fresh public `git clone` gives `public/core` + `public/standard` (release filter drops `extension/` + `personal/`). Marketplace installs land in a category folder via the Store. See `docs/DESIGN.md` "Core vs Personal" + `.claude/rules/store.md`.
