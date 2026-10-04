# WriteDesk (写作台) — standalone Windows exe for writing + jianpu

A slim, double-clickable product build of two EmptyOS apps for a non-technical
(Chinese-speaking, elderly) user: the **writing-editor Documents mode** and the
**jianpu (简谱) composer**. No daemon, no vault, no Python install on the
target machine — one exe + one config file.

## How it works

```
WriteDesk.exe (PyInstaller onefile)
├── launcher.py    boots uvicorn on a free localhost port, opens an Edge
│                  --app window (dedicated profile → mic permission persists,
│                  process handle waitable); window close = shutdown
├── server.py      FastAPI mini-server implementing ONLY the API surface the
│                  two pages call: articles/songs CRUD (markdown + frontmatter
│                  in Documents\WriteDesk\), Whisper dictation, revise/lint
│                  (OpenAI REST), settings.json, i18n cache + LLM fallback,
│                  plus stubs for the endpoints eos.js probes at boot
└── assets/        ASSEMBLED AT BUILD TIME from the repo — never hand-edited:
    ├── writing-editor.html   apps/.../writing-editor/pages/index.html, patched
    │                         (compose button removed, PDF button → print view)
    ├── jianpu.html           apps/.../jianpu/pages/index.html, patched
    ├── jianpu-render.js      copied verbatim (standalone by design)
    ├── print.html            copied verbatim
    ├── home.html             product landing (two big buttons, authored here)
    ├── article-print.html    product print view for articles (authored here)
    ├── prompts.json          REVISE_SYSTEM etc., ast-extracted from app.py
    └── static/               theme.css + eos*.js/css copied from the repo
```

**No forked UI.** `build.py` copies the live app pages and applies mechanical
patches; each patch asserts its marker string, so an upstream page edit that
invalidates a patch fails the build loudly.

PDF = the system print dialog ("Microsoft Print to PDF") via the print views —
no Chromium bundled, keeps the exe ~20MB.

## Build

```
python products/writedesk/build.py             # release build (no console)
python products/writedesk/build.py --console   # debug build (console window)
python products/writedesk/build.py --assets    # assets only; then: python launcher.py --no-window
```

Output: `products/writedesk/dist/WriteDesk/` → `WriteDesk.exe` + `config.toml`
+ `使用说明.txt`. Zip that folder and ship it.

## Setting up the target PC

1. Copy the `WriteDesk` folder anywhere (e.g. `C:\WriteDesk`).
2. Fill `openai_api_key` in `config.toml` (enables 🎤 dictation + ✨ polish;
   everything else works without it).
3. Run once **connected to the internet** — the English UI chrome translates
   itself to Chinese on first load and caches to `i18n-zh.json` next to the
   exe. (Optionally run once on the build machine and ship the cache file.)
4. First dictation → Edge asks for mic permission once; it persists in the
   `browser-profile/` folder next to the exe.
5. Right-click `WriteDesk.exe` → Send to → Desktop shortcut; rename it 写作台.

Files land in `Documents\WriteDesk\{articles,jianpu}\*.md` — EmptyOS-compatible
frontmatter (block-style tags), so they can be dropped into a real vault later.

## Testing

```
python products/writedesk/build.py --assets
cd products/writedesk && python launcher.py --no-window --port 9181
# probe http://127.0.0.1:9181/ ; POST /api/quit to stop
dist\WriteDesk\WriteDesk.exe --no-window --port 9182   # frozen smoke test
```
