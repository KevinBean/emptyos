---
paths:
  - "apps/**/music-studio/**"
  - "apps/**/podcast/**"
  - "apps/**/studio/**"
  - "apps/**/publish/**"
  - "plugins/comfyui/**"
  - "plugins/voice-api/**"
  - "plugins/edge-tts/**"
  - "plugins/footage/**"
  - "emptyos/sdk/media/**"
  - "emptyos/sdk/video_frames.py"
  - "emptyos/sdk/tts_cache.py"
  - "scripts/measure_repaint.py"
  - "skills/creative-*/**"
  - "skills/tool-*/**"
---

# Media Gotchas — ComfyUI, image/video generation, TTS, slideshow assembly

Split out of `dev-gotchas.md` (2026-09-25) to keep the always-loaded rule set
under Claude Code's instruction budget. Loads when you touch a media path; MV
work that happens in the vault does not trigger it, so read this file by hand
before any MV / podcast / ComfyUI / I2V session.

## ComfyUI + image-model integration

- **FLUX image models** — cfg=1–4, euler sampler, simple scheduler (NOT SD1.5 params)
- **ComfyUI `filename_prefix` — only `%year%`/`%month%`/`%day%`/`%hour%`/`%minute%`/`%second%`/`%width%`/`%height%` are substituted.** `folder_paths.compute_vars` (verify there before adding a token) leaves anything else *verbatim in the folder name*, so the `%date:yyyy-MM%` form seen in ComfyUI docs and custom nodes becomes a literal directory containing a colon — illegal on Windows, so it fails **every** save rather than degrading. A prefix may contain `/` to nest (`eos/%year%-%month%/eos`).
- **A ComfyUI output in a subfolder needs `subfolder=` on `/view` — the joined path as `filename=` 404s.** `/view` takes the basename and the subfolder as separate query params (and rejects a `filename` starting with `/` or containing `..`). History items report `subfolder` with the OS separator (`eos\2026-07` on Windows), so normalise before splitting. The plugin keeps a single-string return contract by joining subfolder+name and splitting again at the fetch boundary (`_item_path` / `_view_params`) — so app callers passing that string back to `download_image`/`get_image_url` need no change.
- **ComfyUI's HTTP server usually comes up *after* EmptyOS, and the plugin only registers its `draw`/`animate` providers if it was reachable at `connect()`.** Lose that race and `self.draw()` finds no local provider and silently falls through to paid OpenAI (~$0.04/render) with an idle GPU sitting there — observed 2026-07-25 with `draw` holding only `openai-gpt-image-1` and `animate` empty. `ensure_available()` re-registers on demand (and is idempotent); guard draw calls with it as `podcast`, `publish`, and `studio` do.
- **ComfyUI already logs its own per-prompt timings — don't conclude "we have no performance data" from a stale `comfyui.log`.** It writes a timestamped log to `ComfyUI/user/comfyui.log` (path printed in its own boot banner) carrying `Prompt executed in N seconds` plus the it/s sampler line, and rotates it itself to `.prev` / `.prev2`. Two traps sit on top of that. It keeps only **3 generations**, so a few restarts in one evening erase months of renders — verified 2026-07-31, where three restarts left all three files stamped that hour. And `/history`, the other timing source, is **in-memory per process**, so the same restarts return it to 0 entries. The file in the portable root (`D:\ComfyUI_windows_portable\comfyui.log`, fed by `restart.bat` and the plugin's `auto_start`) is a *different* file and carries only the boot/crash output that precedes ComfyUI's own logger. A GPU-optimisation plan was built twice on "there is no it/s data — discussing efficiency is guesswork"; the data existed the whole time and was being aged out faster than anyone read it. Copy a generation aside **before** restarting if you intend to compare.

## Media — slideshow + video generation

These bit the podcast → slideshow/MP4 path (2026-07); they apply to **any**
audio→scenes→video pipeline (podcast, music-video, footage slideshows).

- **An image-to-video model cannot execute a camera move, and asking for one
  destroys the shot.** A prompt like "camera cranes up to reveal the city" asks
  the model to invent space that is not in the source still, so it hallucinates
  the geometry instead. Measured 2026-07-25 on 无所住 scene 02, same model +
  seed + settings, only the prompt differing: the crane-up prompt turned a wet
  street into a rotated rooftop with the subject walking through a white void,
  while a locked-off prompt held every landmark for the full 5s. All 14 of that
  song's scene prompts had been written as camera moves by an LLM told to
  produce "one concrete **cinematic** image" — the word invites cinematography.
  Describe motion WITHIN the frame (rain running, branches swaying, a figure
  walking away); the camera is on a tripod. Enforced by `NO_CAMERA_MOVES` in
  `apps/personal/music-studio/prompts.py`, composed into both scene planners.
- **Every I2V latent node constrains frame count to a grid AND dimensions to a
  step of 32, and violating either fails silently rather than degrading.**
  LTX-2.3 (`EmptyLTXVLatentVideo`) wants `8n+1` frames; Wan 2.2
  (`Wan22ImageToVideoLatent`) wants `4n+1`. Every `8n+1` is also a `4n+1`, so
  snap to the stricter grid and a caller never has to know which workflow is
  active — `emptyos/sdk/video_frames.py` (`snap_frames`, `dims_on_grid`).
  Non-obvious corollary: **exact 16:9 with both dimensions on a 32-grid only
  exists where the width is a multiple of 512** (width×9/16 must also divide 32,
  and gcd(9,512)=1), so a preset ladder is 512×288 / 1024×576 / 1536×864 — never
  768×432 or 1920×1080. Three shipped presets carried off-grid sizes while the
  file's own comment stated the constraint.
- **Frame count is a VRAM budget, not a model capability — cap it, and make the
  cap operator-tunable.** `num_frames = duration × fps` looks right and is a
  trap: a 15s scene at 24fps requests 360 frames, roughly 3× what a 16GB card
  holds. `comfyui.animate()` returns `""` on failure by design (so the
  capability chain doesn't fall through), so an over-budget scene produced **no
  clip and no error** — 6 of 14 clips went missing with an empty log. Always log
  loudly at the caller when animate returns empty, and read the ceiling from
  config (`[apps.<id>] video_max_frames`) so a GPU upgrade isn't a code edit.
- **A shot that needs a chain does not animate — the real I2V boundary is the
  per-generation length, not any budget band.** Across every motion proof on
  disk (n=6), every shot fitting **one** generation passed and every chained shot
  failed **at segment 1**, never later: 4.2s and 4.1s passed; 6.6s, 9.8s, 13.5s
  and 22.5s all failed. Each segment inherits the *complete* scene's pace by
  design (`visual.py:7469`, load-bearing — it is what keeps chained segments
  coherent), so segment 1 of a 5-segment 22.5s shot must show one fifth of an
  already-`minimal` 0.35 budget. One scene's four attempts were rejected as
  "local motion is too fast for the scene duration", then "no independent motion
  after stabilizing the frame", then frame corruption, then too-fast again —
  **too fast then too slow, alternating, is the signature of an empty band**,
  not of a seed worth retrying. Route by `duration <= video_max_frames / fps`.
- **Threading an operator ceiling through *most* call sites is worse than not
  having the knob.** `video_max_frames` was read at three sites and hardcoded at
  the fourth — the one deriving `generation_seconds` for the motion budget. So
  raising it planned longer segments while the budget was still sized for the
  5.04s default: motion scaled for a clip length that no longer existed, silently.
  Pinned by an AST test asserting `DEFAULT_MAX_FRAMES` is only ever an
  `app_config` default, never a live divisor.
- **Pick proof/sample representatives across the range, not from the top.** MV
  motion proof selected the three *longest* shots, so `0/3` described the worst
  case and was misread as a whole-film wall while six mid-length scenes went
  untested. A sample drawn from one end answers a narrower question than the one
  being asked.
- **A visually continuous I2V hand-off can still stop or change speed.**
  Last-frame conditioning makes the next segment's frame zero overlap the
  previous segment's last frame. Copy-concatenating both creates a one-frame
  velocity collapse, while a small seam pixel delta says nothing about whether
  the next generation restarts at half or three times the prior motion speed.
  Music Studio uses `concat_continuous_segments` to drop the one overlap frame,
  rebuild native-CFR timestamps, and then measures optical flow before, across,
  and after every boundary. Reject both a boundary stall and a material
  pre/post speed ratio; do not hide either with a dissolve. Plain `setpts`
  duration fitting is also invalid because it produced nominal rates such as
  22.3/23.5/28.6 fps. Select native tail-handle frames first; bounded short
  recovery must return an exact native-CFR frame count and pass the same seam,
  repetition, and motion gates after fitting.
- **A media path is not a media version.** A still can be replaced in place,
  while a resumed MV still carries an old scene-number/attempt selection that
  points at a prior art-repair candidate. Keying reuse by pathname, scene
  number, timestamp, or attempt number lets the old candidate silently replace
  the new source and can even turn the stale selection into an unconditional
  pass. Bind every reusable candidate to its own content hash, the source
  still's content hash, and the current semantic generation contract. A
  still-affecting scene edit must automatically rewind that scene and clear its
  selections; a same-path byte change and a legacy hashless record must force
  full review. Motion-only edits should not redraw the still.
- **A `held`-cadence film is structurally incompatible with I2V motion proof —
  the fix is the video strategy, not the shot length.** The motion budget drops
  to its tightest band (`minimal`, scale 0.35) above **8.0s**, and stays there
  for every longer shot: 8.0s → `low` 0.45, 8.1s → `minimal` 0.35, and 11s,
  13.5s and 21.5s are all identical. So the cliff is at 8s, and past it there
  is no gradient to tune against.
  Porch-Light-Low's own treatment specifies `held` cadence — "measured phrase
  floor is 7.5s against a 3.7s bar, so a held pace band (1.5×) yields ~11s
  shots … **this is correct for the song and must not be traded down**". Every
  shot the film is *supposed* to have therefore lands in the minimal band,
  while the motion floor still demands visible independent motion.
  **Shortening the shots is not the answer** — at the treatment's own ~11s they
  are still `minimal`, and reaching `low` would mean going under 8s, which the
  treatment explicitly forbids. Do not recommend it (this was recommended once
  before checking the bands, and it would not have worked).
  What the catalogue already shows: 零偏置 track 8 「Log Out」 — also
  contemplative — shipped via the **Blender-authored path**, where geometry and
  camera are authored and ComfyUI supplies bounded appearance, so no I2V motion
  gate applies. For a held film that is the working route. The alternative is
  re-calibrating the budget bands, which changes every song's envelope and
  should not be done to unblock one film.
- **A long, deliberately-still shot can be squeezed out of existence between
  the motion floor and the motion budget.** `motion_budget_for_generation`
  derives its envelope from `reference_seconds = max(proof_duration,
  scene_duration, ...)`, so the *scene's* length sets the cap even when only a
  5.1s representative is being rendered. Anything past 8s lands on
  `level: minimal, scale: 0.35` — the tightest envelope the system has — while
  the motion floor still demands visible independent motion. For a
  contemplative film the two bounds close on each other.
  Measured on Porch-Light-Low 2026-08-01, whose art direction is explicitly
  "Ozu-adjacent stillness" with "air moving something light" as its baseline.
  All three proof representatives are 9.8-21.5s and all three got
  `minimal / 0.35`. The failures then **alternated between the two bounds**,
  which is the tell: scene 1 went "no independent motion (changed=0.8%)" →
  relax the floor → "local motion is too fast (p95=21.6px/s)"; scene 10
  oscillated "excessive local deformation" ↔ "no independent motion". Three of
  the four historical video runs also died at `motion-proof`.
  This is not promptable and not tunable by amplitude — chasing it reproduces
  the 那道彩虹 re-staging loop. The real levers are the scene's *duration*
  (a cut decision), or the budget model's use of scene length to cap a short
  proof. Recognise the alternating-bound signature and stop.
- **`visible_faces` is a model's claim, not a measurement — a scene can get
  stuck on a hallucinated face.** The art gate hard-fails a `face_visible:
  false` scene when the reviewer reports `visible_faces > 0`, which is right and
  is what caught real faces on Porch-Light-Low scenes 2/7/10 (2 faces each).
  But the count has no independent check behind it, unlike `unexpected_character`
  — which the normalizer overrides using the reviewer's own `visible_people`
  count. Measured 2026-08-01 on scene 10: **three separately generated images**,
  each verified by eye to contain no face (three-quarter rear; pure back of
  head; head leaned back and occluded by the chair, only the crown showing),
  each returned `visible_faces: 1` with "the face is partially visible". The
  binding was hash-verified each time, so the gate was judging the right file.
  When regeneration stops moving the verdict, stop regenerating: an image
  reviewer that has decided a shot contains a face will keep deciding it.
  The escape is `art_waiver` — and `character_mismatch` is deliberately
  **excluded** from the waivable set, because occupancy is meant to be an
  observable fact rather than taste. That exclusion is right in general and
  is the thing that blocks this case, so clearing it is a human call.
- **FLUX will not hold partial-body human framing — route those shots to another
  image model.** A still written as "only her forearms on the rail", "her hands
  in her lap, face out of frame", or "seen entirely from the back" comes back
  with the figure *completed* and a face in shot. Measured on Porch-Light-Low
  2026-08-01 against a contract reading "No face appears anywhere in this film.
  Not once, at any scale.": art review went 8 → 4 → 3 failures as prompt fixes
  landed, and **the three survivors were the only three shots in the film
  containing a human body**, all failing on `character_mismatch`. Everything
  environmental or object-based cleared.
  Prompt work helps but does not close it: adding face/portrait terms to the
  negative prompt for `face_visible: false` scenes took `visible_faces` from 2
  to 1 per shot and no further. Attempts 4-6 are wasted GPU — the ceiling is the
  model, not the wording.
  Do NOT respond by loosening the art gate; refusing these is the gate working,
  and `visible_faces` is what makes the defect nameable at all (before it, the
  gate could only report the wrong *scale*). Source the shot from a model that
  does posture — the consent-gated `openai-gpt-image-1` `draw` provider, or a
  subscription-generated file — and bring it in through `still_replacements`,
  which marks it **director-supplied** so the repair loop cannot regenerate over
  it. Worked example + ready prompts:
  `{vault}/10_Projects/YouTube-Music-Channel/songs/Porch-Light-Low/stills-to-source-externally.md`.
- **Two daemons sharing one ComfyUI cannot both load FLUX on a 16GB card.** The
  still model stages `Flux 11350MB` + `FluxClipModel_ 4777MB` = **16.1GB against
  a 16.376GB card**, so it only fits when nothing else is resident. With
  `[plugins.comfyui] feature.model-residency.enabled = true` on the main daemon
  and `feature.gpu-arbiter.enabled = false`, a second consumer (a sandbox-pool
  member) wedges ComfyUI: HTTP stops answering, its log freezes mid-stage at
  `Model Flux prepared for dynamic VRAM loading`, and the GPU pins at 100%.
  Measured twice on 2026-08-01 — once it never recovered in 40 minutes and
  needed a manual restart; once it recovered on its own after the client was
  killed. The same render on `:9000` alone runs fine. So **verify GPU work on
  the main daemon, not on a leased sandbox member**, or enable the arbiter.
- **A lower-precision weight can be FASTER, when the card and not the math is
  the bottleneck — compare the resident set at peak stage, not file sizes.**
  Measured 2026-08-15 on MiniMax Music 3: the INT8 DiT beat the fp16 DiT
  **3:29 vs 16:44** on the same cache-miss autoregressive pass, 4.8× in favour
  of the *lower* precision. Mechanism, which is the transferable part: fp16
  stages 4685MB during the AR pass against INT8's 2385MB, and on a 16.376GB
  card that 2.3GB delta evicts the 8.7GB text encoder into ComfyUI's dynamic-
  VRAM streaming. The slowdown is paging, not arithmetic. "The 4.91GB fp16 file
  fits alongside 11.9GB" is a file-size argument, not a memory-system one —
  precision selection on a VRAM-bound card is a memory decision.
- **ComfyUI caches node outputs, so an A/B timing comparison is invalid until
  you have positive evidence the stage under test actually ran.** Two runs
  reusing the same caption + lyrics + seed skip the text-encode/AR stage
  entirely and measure only the tail. On 2026-08-15 this produced a confident,
  stated-then-retracted "fp16 is 2.9× faster" — the exact inverse of the row
  above, because the fast runs were cache hits. Vary an input the cache keys
  on, or restart, and read the stage's own duration out of `comfyui.log` rather
  than wall clock. Generalises to any memoised executor (make, dbt, Nix, a
  `Pipeline` resume). Sibling of the false-positive discipline in
  `.claude/rules/audits.md`: that one is a heuristic firing on healthy input,
  this one is a number that looks healthy because nothing ran.
- **A generated song ignores its lyrics when `max_duration` is too small for
  them.** MiniMax emits 25 audio frames/second, so a 60s cap is ~1500 tokens —
  hand it a 40-line lyric and it abandons the text rather than rushing. Size
  the duration against the lyric, not the other way round. (Diagnosed
  2026-08-15 after a run was nearly recorded as "the model can't follow
  lyrics"; the same model sang 中英混合 lyrics correctly at 120s.)
- **Comparing two versions of an audio artifact needs a phase-invariant metric —
  raw-waveform correlation reverses the verdict.** Any generative audio edit
  (ACE-Step repaint/extend/edit, a codec round-trip, a re-render) decodes the
  whole track through a neural VAE, which reconstructs the waveform without
  preserving phase and can shift it by milliseconds. Measured 2026-08-15 on a
  section repaint that kept 0-20s untouched: waveform Pearson r on that
  untouched head read **-0.25** while log-mel r on the *same audio* read
  **+0.97** — "the whole track was destroyed" vs "the track is intact", from one
  pair of files. Remove global lag by cross-correlation, then compare log-mel
  **magnitude** spectrograms, and read a per-second profile rather than a
  segment average (the profile is what proves the edit landed on the seconds you
  asked for). Never verify a preserved region with a hash: a mismatch there is
  the expected result, not a bug. Tool: `scripts/measure_repaint.py`. This is
  the same family as the it/s and rolloff traps above — a number that looks
  authoritative and is answering a different question than the ear.
- **Model size must be chosen against the card, not by version number.** A
  14.7GB Q3 quant of a 22B model plus a 13.2GB text encoder on a 16GB card means
  ~30GB cycling through VRAM — it thrashes and silently fails. The 3.8GB Q5
  quant of a 5B model (Wan2.2-TI2V-5B) left ~7GB for latents and rendered
  1024×576 / 121 frames successfully on the same hardware, i.e. **2.6× the pixel
  count of what the "better" model managed**. Headroom for latents is what buys
  resolution and clip length; prefer a gentler quant of a smaller model.

- **An audio-driven InfiniteTalk clip is LONGER than you asked for, and the tail
  has no audio under it.** The sampler pads to whole `frame_window_size`
  windows: measured 2026-09-20, 65 → 81, 85 → 153, 181 → 225 and 261 → 297
  frames. Two consequences, both silent. In a cut, the padding is dead picture
  after the line ends — trim each dialogue shot to where its speech actually
  stops (measure the speech span; edge-tts also leaves up to a second of
  trailing silence in the file, so file length is the wrong number twice over).
  In a measurement, pad the guide audio to the **source** frame count, never to
  the output's: scoring the same S2 dub with audio padded to 81 frames instead
  of 65 added 16 silent windows and dropped LSE-C from 4.414 to 3.503, which
  reads as a worse dub rather than as a longer file.

- **`InfiniteTalk_Multi` with two speakers needs EXPLICIT `ref_target_masks`,
  because the documented default is computed too late to be read.**
  `multitalk/multitalk_loop.py:80` does build left-half / right-half face boxes
  when masks are absent — the branch runs, and that is what makes the bug
  confusing. But it writes them back into `multitalk_embeds`, and
  `nodes_sampler.py:586` already read that key (`None`) out of the same dict and
  baked it into the model params at line 1456 — both **before** the dispatch to
  `multitalk_loop` at line 2045. Nothing re-reads the dict, so the model gets
  `None`, `model.py:1185`'s `elif ref_target_masks is not None` guard never
  fires, `x_ref_attn_map` stays `None` from line 1095, and the audio
  cross-attention called at `model.py:1346` raises `'NoneType' object has no
  attribute 'max'` at `multitalk/multitalk.py:343`. It reads like a model bug
  and is a wiring one. Supply three masks — speaker 1, speaker 2, background —
  as a MASK batch (`LoadImage` ×3 → `ImageBatch` ×2 → `ImageToMask`). **Row
  order is load-bearing**: `multitalk.py:350-351` maps `x_ref_attn_map[0]` to
  human 1 and `[1]` to human 2, so mask 1 drives `audio_1` and mask 2 drives
  `audio_2`. The harness builder does this and refuses a `multi` build without
  them (`…/lipsync-test-20260916/harness/infinitetalk_workflow.py`).

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

