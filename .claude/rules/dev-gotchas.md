# Development Gotchas — Non-Architectural Surprises

Keep CLAUDE.md focused on EmptyOS-architectural rules. Generic Python/web/integration quirks live here.

## Frontend / Web

- **Windows paths** — normalize with forward slashes (`EOS.normPath()`, `.replace("\\", "/")`)
- **HTML pages hot-reload** — read from disk per request; Python changes still need server restart
- **`encodeURIComponent` encodes `/`** — use a custom encoder for Obsidian URIs
- **TTS audio** — must be copied to a servable dir + served via `/api/audio/{filename}`

## Python / Runtime

- **`config.path` is the config FILE**, not the project root — use `config.path.parent` for project dir
- **App relative imports** (`from . import module`) require `app_loader` to register parent packages in `sys.modules`
- **Billing stats / assistant sessions** are cached in memory; billing flushes every 10 calls
- **Never name a code/dataset directory `data/`** — the root `.gitignore` `data/` pattern matches ANY dir named `data` at ANY depth, so `engines/<x>/data/` or `apps/<x>/data/` source files are **silently untracked** (everything works locally, vanishes from clones/releases). Bit three engines (thermal, soil, overhead_line) across separate sessions. `.gitignore` now carries `!engines/*/data/` for engine reference datasets; everywhere else prefer a different name (`tables/`, `datasets/`, a flat `standard_tables.py`). After creating any new directory, sanity-check with `git check-ignore -v <path>`.
- **The same trap catches *files*, not just dirs** — `.gitignore` has `scripts/_*.py` (scratch scripts), so a shared helper named `scripts/_common.py` is silently untracked: every importer works on your machine and `ImportError`s in a fresh clone or a release snapshot. Name shared script helpers without the underscore, following `scripts/{hook,suno,check}_common.py`. Run `git check-ignore -v <path>` on any new **path** — file or directory — before other code imports it.

## Integrations

- **Claude CLI** — needs `--dangerously-skip-permissions` + `cwd=vault_path` for vault access
- **FLUX image models** — cfg=1–4, euler sampler, simple scheduler (NOT SD1.5 params)
- **Staff agents** — global Claude lock → sequential execution; output to `system-log` (system feed), NOT `capture` (user inbox)
- **Saved staff agents** in `data/apps/staff/agents.json` override `DEFAULT_STAFF` (defined in `apps/personal/staff/agents.py`) — edit both when changing agent configs

## Media — slideshow + video generation

These bit the podcast → slideshow/MP4 path (2026-07); they apply to **any**
audio→scenes→video pipeline (podcast, music-video, footage slideshows).

- **Slideshow needs per-segment timings, and single-file TTS gives none.** The
  `has_slideshow` gate is `timings AND scenes AND any(images)` — an empty
  timeline forces audio-only. Per-turn TTS+stitch yields one clip per line
  (exact durations via `compute_timings`); **VibeVoice single-pass dialogue
  emits ONE combined file with every segment's `audio=""`**, so there are no
  per-turn files → `compute_timings([])` → `[]`. Fall back to
  `proportional_timings(audio_duration_ms(full), script)` (distribute the full
  duration by text length) so the timeline spans the whole audio. Real
  word-level sync (Phase 3) needs ASR — the `listen` capability with whisper
  `verbose_json` segment timestamps. Helpers: `emptyos/sdk/media/subtitles.py`.
- **ffmpeg concat DEMUXER mistimes `duration` directives when input images
  differ in size** — the killer for a slideshow that mixes wide diagrams with
  square AI scenes: the video stream collapses to ~1/N length then freezes on
  the last frame while audio plays on (the *container* still reports full
  length — check the **video stream** duration, `ffprobe -select_streams v:0`).
  Fix: use the concat **FILTER** with one `-loop 1 -t <dur> -i img` input per
  scene, each `scale=…:force_original_aspect_ratio=decrease,pad=…,setsar=1,
  fps=30` to a uniform frame BEFORE `concat=n=N`. The reference impl is
  `emptyos/sdk/media/video.py::concat_clips` — new video assembly should follow
  it (or reuse it), never hand-roll the raw `-f concat` demuxer over
  heterogeneous images. (The concat demuxer IS fine for **uniform, pre-rendered
  clips with `-c copy`**, e.g. `music-studio/assembler.py` — the bug is
  mixed-size images held by `duration`.)
- **Never crop the graphs — letterbox.** `scale=1080:1080` alone *stretches*;
  `object-fit: cover` *crops*. For information-bearing images (diagrams,
  charts) use fit-inside + pad (`force_original_aspect_ratio=decrease` + `pad`
  in ffmpeg; `object-fit: contain` in the player) so nothing is lost. Carry a
  per-scene `fit` flag (`contain` for graphs, `cover` for full-bleed AI scenes)
  from the pipeline → `slideshow.json` → the player (`slideshow-player.js` /
  `eos-deck.js` read `scene.fit`).
- **VibeVoice bakes sighs + hiss into podcast audio — retired for the podcast
  app (2026-07-05).** VibeVoice (single-pass multi-speaker TTS via ComfyUI) is a
  *conversational* model: it generates non-verbal sounds (breaths / audible
  **sighs**) at turn boundaries plus a steady synthesis **hiss** — both inherent
  to the model, NOT the script or the reference voices (the reference wavs
  measured clean: silent-gap floor ≈ −62 dB vs speech −20 dB). Output-side
  denoise makes it *worse*: an `afftdn` spectral-subtraction pass warbles
  ("musical noise") against synthetic hiss and introduced an audible artifact at
  one spot (fine on real acoustic hiss, unsafe on synthetic TTS). Fix →
  `[apps.podcast] vibevoice_enabled = false`, which routes `_generate_audio`
  down the **per-turn kokoro** path: each turn is its own clean clip stitched
  with a fixed gap → no "in between" for a sigh + no synthesis hiss, and
  `compute_timings` gives frame-accurate captions/scene-anchoring (vs VibeVoice's
  `proportional_timings` estimate). If VibeVoice is ever re-enabled, only
  `clean_audio(dest, denoise=...)` **de-rumble** runs by default (highpass —
  artifact-free); spectral `afftdn` is opt-in via `[apps.podcast] audio_denoise
  = true`. Kokoro collapses unknown voice names to ONE default voice, so
  distinct two-host voices need real ids — friendly-name aliases
  (`emma→af_heart`, `michael→am_michael`, …) live in `plugins/voice-api/plugin.py`
  `KokoroTTSProvider._ALIASES` (mirrors `openai_tts.py`).
- **Podcast length must be capped, or a long article yields a 10-min episode.**
  Auto-scaling the segment count off article length (with an under-delivery
  continuation that can *overshoot* the request) once produced 48 segments /
  9.6 min from a 2.4k-word post. Target ~half the source length as *dialogue*
  (`DIALOGUE_RATIO = 0.5`), hard-cap at `MAX_AUTO_SEGMENTS = 24`, and trim the
  final combined script (`script[:MAX_AUTO_SEGMENTS]`) so the continuation can't
  blow past the ceiling. `apps/personal/podcast/pipeline.py` + the publish
  `api_generate_podcast` auto branch share the same 0.5×/cap-24 math.

## Architectural — keep these top-of-mind (also in CLAUDE.md)

- **Vault read-modify-write races** — see CLAUDE.md § Development Gotchas
- **Vault frontmatter tags must be block-style** — see CLAUDE.md § Development Gotchas
- **Normalize loose field shapes at the write boundary** — see CLAUDE.md § Development Gotchas
