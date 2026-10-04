# Third-Party Licenses

EmptyOS itself is released under the license noted in the top-level
[LICENSE](LICENSE) file. This document attributes the third-party software
EmptyOS depends on, satisfying the notice clauses in their licenses (Apache
2.0 §4, BSD-3 §3, etc.) for any downstream redistribution.

Audit this file with:

```
python scripts/check-licenses.py
```

The audit flags forbidden licenses (GPL/AGPL/SSPL), missing entries here,
and unused declarations. Re-run before any public release.

---

## EmptyOS-authored content

These belong to the EmptyOS project — they are not third-party:

| What | Where |
|------|-------|
| EmptyOS kernel, capabilities, SDK | `emptyos/` |
| Bundled apps + plugins (`core` tier) | `apps/`, `plugins/` |
| Web frontend (shared components, theme tokens) | `emptyos/web/static/` |
| ARPABET phone descriptions, IPA↔ARPABET alias tables | `services/pronounce/server.py`, `apps/personal/shadowing/app.py` |

## User-generated content

These belong to the user, not to EmptyOS:

| What | Notes |
|------|-------|
| Vault notes (`{vault}/**/*.md`) | Plain markdown owned by the vault holder. EmptyOS reads + writes but claims no ownership. |
| LLM output (`think` capability results) | Owned by the user per OpenAI / Anthropic / etc. terms. Local-LLM (Ollama) output has no provider claim — trivially the user's. |
| Audio recordings (`{vault}/**/recordings/`, `data/apps/**/audio/`) | The user's voice + speech. EmptyOS stores; the user owns. |

## Bundled data

Third-party datasets redistributed inside this repository, as opposed to
downloaded at runtime.

| Dataset | License | Where | Notes |
|---|---|---|---|
| CMU Pronouncing Dictionary | [BSD-2-Clause](http://www.speech.cs.cmu.edu/cgi-bin/cmudict) | `apps/public/englishos/soundcheck/bank/` | Word/phone data derived by `scripts/build_soundcheck_bank.py`. The notice travels with it in `bank/CMUDICT-LICENSE.txt`, as clause 1 requires. The generator reads `cmudict.dict` as a file and never imports the GPL-3.0 PyPI wrapper, so nothing links against it and no runtime dependency is added. |

## Model weights

Downloaded at runtime from the upstream registry — not bundled in this
repository:

| Model | License | Source |
|-------|---------|--------|
| facebook/wav2vec2-xlsr-53-espeak-cv-ft | [Apache-2.0](https://huggingface.co/facebook/wav2vec2-xlsr-53-espeak-cv-ft) | HuggingFace; trained on Common Voice (CC0). First-run download, cached under `~/.cache/emptyos/pronounce/`. |
| Kokoro v1.0 ONNX | [Apache 2.0](https://github.com/thewh1teagle/kokoro-onnx) | User-installed under `services/voice-api/models/kokoro/`. |
| Coqui XTTS v2 | [CPML](https://coqui.ai/cpml.txt) | Coqui Public Model License — research / non-commercial. Optional GPU voice-cloning provider; not loaded by default. |
| faster-whisper (Systran/faster-whisper-*) | [MIT](https://github.com/SYSTRAN/faster-whisper) | Downloaded on first STT call. |

The `wav2vec2-xlsr-53-espeak-cv-ft` model card lists Apache-2.0 (checked 2026-09-29); the underlying
Common Voice corpus is CC0. Both permit unrestricted use, including
commercial.

The Coqui XTTS license (CPML) restricts to non-commercial use. EmptyOS does
not bundle XTTS weights; the `XTTSProvider` in `plugins/voice-api/` is
unreachable until a user registers a custom voice. If you redistribute
EmptyOS commercially, either omit XTTS or check Coqui's current terms.

---

## Python runtime dependencies

Audited via `pip show <package>` (License-Expression / License /
Classifier metadata) and `scripts/check-licenses.py`.

### MIT-licensed

| Package | Version pin | Source |
|---------|-------------|--------|
| [fastapi](https://github.com/tiangolo/fastapi) | `>=0.115` | Web framework |
| [anthropic](https://github.com/anthropics/anthropic-sdk-python) | `>=0.40` | Claude API SDK |
| [transformers](https://github.com/huggingface/transformers) | (pronounce) | wav2vec2 model loader |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | (voice-api) | Local STT |
| [prompt_toolkit](https://github.com/prompt-toolkit/python-prompt-toolkit) | `>=3.0` | CLI prompt rendering |
| [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) | (voice-api) | Kokoro TTS inference |
| [firecrawl-anydoc](https://github.com/firecrawl/anydoc) | `>=0.2.4,<0.3` | Legacy-document to markdown (`legacy-doc` plugin); local only, OCR never requested |

### LGPL / weak-copyleft

| Package | License | Source |
|---------|---------|--------|
| [pyzmq](https://github.com/zeromq/pyzmq) | LGPL-3.0 (with BSD-3 extension for binding linking) | Python binding for ZeroMQ. Dynamic linking from Python is allowed by the BSD extension; static linking would require LGPL §6 compliance. |

### GPL — plugin-only / opt-in (do NOT bundle in public release)

| Package | License | Status |
|---------|---------|--------|
| [edge-tts](https://github.com/rany2/edge-tts) | **GPL-3.0** | Python wrapper around Microsoft Edge TTS. Loaded by the `edge-tts` plugin only. Public release must either omit the plugin from the default tier or surface a GPL notice at install time. |
| [cmudict](https://github.com/prosegrinder/python-cmudict) | **GPL-3.0** | The CMU Pronouncing Dictionary **data** is BSD-2-Clause (Carnegie Mellon; the notice ships as `apps/public/englishos/soundcheck/bank/CMUDICT-LICENSE.txt`); only the Python wrapper is GPL. Used by `services/pronounce/`, which ships in the public EnglishOS edition since editions-build A2 (Kevin, 2026-09-29). Accepted there because EmptyOS is AGPL-3.0-or-later, with which GPL-3.0 combines (GPLv3 §13), and the wheel is never bundled: the `pronounce` plugin ships off (`default_enabled = false`) and a user who turns it on installs the wrapper themselves from `services/pronounce/requirements.txt`. Only a permissively licensed build would still need the data vendored + a ~30-line lookup helper in `services/pronounce/g2p.py`. |

### Apache 2.0

| Package | Version pin | Source |
|---------|-------------|--------|
| [aiohttp](https://github.com/aio-libs/aiohttp) | `>=3.9` | Async HTTP client/server |
| [apscheduler](https://github.com/agronholm/apscheduler) | `>=3.10` | Job scheduling |
| [huggingface_hub](https://github.com/huggingface/huggingface_hub) | (pronounce) | Model downloads |
| [watchfiles](https://github.com/samuelcolvin/watchfiles) | `>=0.21` | Vault watcher |
| [wordfreq](https://github.com/rspeer/wordfreq) | `>=3.1` (`english` extra) | Word frequencies for the dictionary (reading bar, suggestion ranking) and the soundcheck bank build. The **code** is Apache-2.0; its frequency **data** is CC BY-SA 4.0 and is installed by pip, not redistributed here. The soundcheck bank stores no frequency values (dropped at build, `BUILD_ONLY_KEYS`); it keeps only the 1-5 difficulty band each item was rated with. |
| [python-multipart](https://github.com/Kludex/python-multipart) | `>=0.0.9` | File uploads |

### BSD (2-clause / 3-clause)

| Package | Version pin | Source |
|---------|-------------|--------|
| [uvicorn](https://github.com/encode/uvicorn) | `>=0.30` | ASGI server |
| [websockets](https://github.com/python-websockets/websockets) | `>=13` | WebSocket support for uvicorn (realtime `/ws`) |
| [typer](https://github.com/tiangolo/typer) | `>=0.12` | CLI framework |
| [rich](https://github.com/Textualize/rich) | `>=13` | Terminal rendering |
| [torch](https://github.com/pytorch/pytorch) | (pronounce) | Tensor library / wav2vec2 inference (modified BSD) |
| [soundfile](https://github.com/bastibe/python-soundfile) | (pronounce) | Audio I/O |
| [markdown](https://github.com/Python-Markdown/markdown) | `>=3.4` | Markdown → HTML |
| [python-docx](https://github.com/python-openxml/python-docx) | `>=1.1` | DOCX read/write |

### ISC

| Package | Version pin | Source |
|---------|-------------|--------|
| [librosa](https://github.com/librosa/librosa) | (pronounce) | Audio loading + resampling |

### Python Software Foundation / Public Domain

| Package | Version pin | Source |
|---------|-------------|--------|
| [pyyaml](https://github.com/yaml/pyyaml) | `>=6` | YAML parser |

### Optional / dev / extras

| Package | License | Notes |
|---------|---------|-------|
| [httpx](https://github.com/encode/httpx) | BSD-3-Clause | dev extra — async HTTP client used in tests |
| [pytest](https://github.com/pytest-dev/pytest) | MIT | dev extra — test runner |
| [pytest-asyncio](https://github.com/pytest-dev/pytest-asyncio) | Apache-2.0 | dev extra — async test support |
| [pytest-playwright](https://github.com/microsoft/playwright-pytest) | Apache-2.0 | dev extra — browser tests |
| [ruff](https://github.com/astral-sh/ruff) | MIT | dev extra — linter/formatter |
| [numpy](https://github.com/numpy/numpy) | BSD-3-Clause | semantic / fem extra — array ops |
| [scipy](https://github.com/scipy/scipy) | BSD-3-Clause | fem extra — scientific computing |
| [faiss-cpu](https://github.com/facebookresearch/faiss) | MIT (with BSD-3 components) | semantic extra — embedding index |
| [opencv-python](https://github.com/opencv/opencv-python) | Apache-2.0 | camera extra — image capture |
| [ezdxf](https://github.com/mozman/ezdxf) | MIT | dxf extra — DXF read/write |
| [gmsh](https://gmsh.info/) | **GPL-2.0+** | fem extra — **GPL; opt-in user install only** |

### Notes on edge cases

- **`edge-tts`** — the Python wrapper carries **GPL-3.0**, but it's not
  linked statically into the EmptyOS process; the `edge-tts` plugin shells
  out to it as a subprocess. We treat it as an optional, user-installed
  service rather than a bundled dep. Public distribution can either omit
  the `edge-tts` plugin or pip-install at runtime.

- **`cmudict`** — the **Python package** is GPL-3.0, but the CMU
  Pronouncing Dictionary **data** it bundles is BSD-2-Clause. It stays a
  plugin-style dep that ships with `services/pronounce/`, not the main
  daemon, and is user-installed — accepted for the AGPL public release
  (editions-build A2, 2026-09-29). If GPL ever becomes a blocker (a
  permissive build), vendor the JSON data + write a 30-line lookup helper
  directly in `services/pronounce/`.

- **`pyzmq`** — LGPL-3 with a BSD extension that allows linking against any
  app. Dynamic linking from Python is universally fine; static linking
  would require honouring LGPL §6 — out of scope for our packaging.

- **`phonemizer`** (GPL-3.0) — **not used**. Installed in some dev envs as
  a transitive of `transformers`'s Wav2Vec2PhonemeCTCTokenizer, but our
  service deliberately bypasses that tokenizer (reads `vocab.json`
  directly) so we don't link against it. Will be dropped from
  `services/pronounce/requirements.txt`.

- **`g2p_en`** (Apache 2.0) — **not used**. Was the original g2p choice
  but is incompatible with Python 3.13. Replaced by `cmudict`. Will be
  dropped from `services/pronounce/requirements.txt`.

---

## Optional extras

Loaded only when the corresponding `[project.optional-dependencies]` group
is installed.

| Extra | Package | License |
|-------|---------|---------|
| `dev` | pytest, pytest-asyncio, pytest-playwright, ruff, httpx | MIT / Apache 2.0 |
| `semantic` | faiss-cpu | MIT |
| `semantic` | numpy | BSD-3-Clause |
| `camera` | opencv-python | Apache 2.0 |
| `fem` | gmsh | GPL-2.0 *(forbidden — kept under `fem` extra only; installation is opt-in for users with their own GPL allowance)* |
| `fem` | scipy | BSD-3-Clause |
| `dxf` | ezdxf | MIT |

**`gmsh` carries GPL-2.0.** This makes the `fem` extra incompatible with
permissive redistribution of any EmptyOS feature that depends on it. The
mainline build doesn't pull `fem` — only users who explicitly
`pip install .[fem]` are exposed. If a public release ever needs `fem` in
the default install, replace gmsh with a permissively-licensed mesher.

---

## Frontend / runtime assets

Bundled in `emptyos/web/static/`:

| Asset | License | Notes |
|-------|---------|-------|
| Leaflet (loaded lazily via CDN) | BSD-2-Clause | Map rendering |
| Leaflet-Geoman (CDN) | MIT | Drawing layer |
| EOS_UI, eos.js, eos-components.* | EmptyOS-authored | See `LICENSE` |

CDN-loaded assets are fetched at runtime from the original publisher's
infrastructure; no copy is hosted in this repo.

---

## Attribution requirements satisfied here

- **MIT**: notice + license text reproduced via the project URL above.
- **BSD-2 / BSD-3**: project name + copyright holders reachable via the
  source URL; downstream redistributors copy this file forward.
- **Apache 2.0 §4(c)**: this NOTICE-equivalent file derives from upstream
  metadata. Any modifications to a third-party dep should be called out
  in a per-dep "Modifications" subsection here.
- **ISC**: notice retained via source URL.
- **LGPL**: dynamic linking only; no static linking.
