# The Local Music Studio — editor design, with Suno as the reference

Status: **repaint built (2026-08-16); the editing loop around it is not.**
This doc is the target shape for the next round, taken from Suno's editor
because it is the interface Kevin already knows and the one the channel's 63
songs were made in.

Not a plan to clone Suno. Suno Studio 2.0 is a DAW — MIDI piano roll, wavetable
synth, 7 built-in effects, automation curves, a 16-track step sequencer. **We
are not building that**, and nothing below proposes it. The borrow is the
*editing loop*, which is small, and which we currently do not have.

## The one pattern that matters

Every generative action in Suno produces **two alternates**, never one, and
never applied in place:

| action | variations produced |
|---|---|
| Replace Section (inpaint) | "Two alternate versions appear in the Edits Library" |
| Remaster | "Creates 2 new versions" |
| Stem Cover | "Two generations per prompt in Take Lanes" |
| Sounds mode | "Two options per prompt" |
| Loop recording | each pass becomes a separate Take Lane |

The loop is always: **generate → two takes → audition each in context against
the surrounding audio → commit one → optionally "Generate More"** (2–6
recommended). Comping goes further: splice preferred *portions* across takes.

This is not a Suno idiosyncrasy, it is the correct shape for a non-deterministic
edit, and it is the same shape EmptyOS already uses everywhere else —
propose / preview / confirm (`.claude/rules/proposed-action.md`). A repaint is
the render-shaped case of that rule: the preview is the audio itself, played in
context. Today `repaint_music()` returns one file and the caller has nothing to
compare it against, which is the single biggest gap between what we built and
what the pain actually needs.

**Cost of adopting it: two seeds instead of one.** A 20s repaint window is 10–30s
of GPU, so a pair is not a meaningful expense. There is no architectural work —
the verb is already pure and returns a path.

## Feature map — Suno → ACE-Step → us

| Suno | ACE-Step node | EmptyOS today |
|---|---|---|
| Replace Section (inpaint) | `ACEStepRepainting` | **built** — `comfyui.repaint_music()` |
| Extend | `ACEStepExtend` (`left/right_extend_length`) | deferred |
| Cover / re-perform with new lyrics | `ACEStepEdit` (`edit_prompt`, `edit_lyrics`, `edit_n_min/max`) | deferred |
| Persona (voice identity) | `ReferenceTimbreAudio` — **core 1.5 path, not the MW pack**, so a different wiring job | deferred |
| Trained style | `ACELoRALoader` | dark — LoRA dir empty, `lora_name` returns `[]` |
| Remaster (production polish) | no equivalent | none |
| Crop / fade / rearrange / split | not generative — plain ffmpeg | none |
| Stems (up to 12) | not ACE-Step | separate concern |

Crop, fade, and rearrange are worth noting precisely because they are **not**
model work. Suno's editor is mostly ordinary audio editing with three
generative verbs embedded in it. A useful local studio is likewise mostly
ffmpeg.

## Controls Suno exposes, and what we can honestly offer

### Variation strength → our `variance`

Suno's Remaster exposes exactly three: **Subtle / Normal / High**, described as
"minimal production change" / "maintains duration and style" / "more noticeable
differences **including possible vocal changes**".

That last clause is the same cliff measured here 2026-08-16. Our float maps to
their three, with the measured numbers behind it:

| preset | `variance` | measured behaviour |
|---|---|---|
| Subtle | 0.15 | words held but smeared (0.612 word-similarity) |
| Normal | **0.30** | words held in place (0.778) — our default |
| High | 0.50+ | **verse restarts** (0.235); instrumental windows only |

Suno names the presets and hides the number. We should do the same: a float
invites a user to pick 0.5 because it looks like the middle, and 0.5 is past
the cliff.

**The two good bands barely overlap, and that is the honest headline.** Running
the shipped default end-to-end 2026-08-16 (two takes, two seeds, 20-40s of a
60s take) the words survived — 0.720 and 0.778 word-similarity — while the
*spectral* change measured only +0.034 and +0.044 separation from the kept
region. Both seeds, so it is systematic rather than luck:

| variance | words | spectral separation |
|---|---|---|
| 0.01 | — | −0.013 (no-op) |
| **0.30 (default)** | **0.720–0.778 held** | **+0.034 / +0.044 — real but subtle** |
| 0.50 | 0.235 (verse restarts) | +0.128 / +0.145 |
| 0.95 | — | +0.329 |

So on **vocal** material the window that keeps the lyric is also a window that
changes the audio only a little. That is a property of the model, not a bug to
tune out, and it sets expectations for the UI: repaint at a lyric-safe strength
is a *nudge*, and a section that needs to be genuinely different needs either an
instrumental window or a re-generation. It is the same shape as the MV motion
cliff (`.claude/rules/dev-gotchas.md`) — two bounds closing on each other with
no gap to aim at.

It also forced a fix in `measure_repaint.py`: a single "changed" threshold
conflates *subtle* with *nothing*, and would have failed every correct
lyric-preserving repaint. It now reports three bands and only gates on
separation ≤ 0.

### Keep Duration toggle

Suno lets a replacement run shorter or longer than the region it replaces. Ours
is always fixed — the repaint mask is a fixed span of latent frames. Honest
status: **we cannot offer this on `ACEStepRepainting`.** The verb that changes
length is `ACEStepExtend`. Don't fake it with a toggle.

### Instrumental Mode toggle

Suno can regenerate a region with vocals removed. For us this is close to free
and matters more than it looks: an instrumental window has **no lyric alignment
to lose**, so it is the one case where high variance is safe. Worth exposing as
the thing that unlocks the top of the strength range.

### What the catalogue can actually supply (measured 2026-08-16)

Repaint requires the original lyrics and a style. Walking the real library
through the running daemon says how often those exist:

| | count | share of audio-bearing songs |
|---|---|---|
| songs in library | 260 | — |
| **with audio** (the only repaintable set) | 102 | — |
| real lyrics (≥200 chars of body) | **62** | 60% |
| any `genre` / `style` / `theme` | **23** | 22% |
| both | 23 | 22% |

Every song carrying a style also carries lyrics, so **style is the sparse
field, not lyrics** — and it is the cheap one to replace with a text input.
Gating the Edit panel on a populated `genre` would cut the usable set from 62
songs to 23 for no reason.

Worse, the record that owns the audio is often not the record that owns the
words. `Let It Glow.md` — the one the player streams — has
`lyrics: "\n\n# Let It Glow\n"`, `style: None`, `genre: ""`, `bpm: ""`, while
the real lyrics and the Suno style prompt live in a sibling note
(`01-let-it-glow.md`) in the same album folder. The Phase-0 gate only worked
because those were extracted by hand.

So the panel must:

* prefill lyrics **when present** (60% of the time) and always show the
  textarea, since the source record frequently has only a heading;
* **refuse to submit on empty lyrics** — that is not a neutral default, it is
  the invented-vocals path (0.149 word-similarity, measured);
* take style as free text with a fallback, never as a required record field;
* say which note the prefill came from, because it may not be this one.

### Replace Lyrics — scoped to the selected region

The most important control on the list, and the one that explains our worst
result. **Suno's lyric box edits the lyrics for the selected region only.**

Ours takes the whole song's lyrics and lets the model re-derive where it is in
them — which is why at variance 0.5 the repainted window sang verse line 1
while the source was at verse line 3. Real words, wrong place. Whether
`ACEStepRepainting` can be given region-scoped lyric conditioning is **untested
and is the first experiment of the next round**; if it can, it likely raises the
usable variance ceiling, because the alignment would no longer be something the
model has to guess.

## What our stack cannot match, and why

- **The window is a different model from the track.** Studio verbs live in the
  MW custom-node pack (ACE-Step 1.0 3.5B); `generate_music` runs 1.5 turbo
  through core ComfyUI. There is no 1.5 repaint node installed. Suno has no
  equivalent seam. Matching it is a listening judgement, not a parameter.
- **Lyric alignment is re-derived, not inherited** (above).
- **No remaster.** Nothing in the pack does production polish while holding the
  performance.
- **No stems.** Different toolchain entirely.

## Next round, in order

1. **Two takes per repaint, auditioned in context, one committed.** The highest
   value per unit of work, and it is the paradigm we already have a rule for.
   Requires a surface — Music Studio has no repaint control at all today.
2. **Named strength presets** (Subtle / Normal / High) replacing the raw float,
   with High gated behind an instrumental-window flag.
3. **Region-scoped lyrics** — test whether the alignment cliff is a consequence
   of whole-song conditioning. This is a measurement, not a build.
4. **`ACEStepExtend`** — the outro/intro verb, and the one that legitimately
   changes duration.
5. Crop / fade / rearrange as plain ffmpeg, if the editing loop gets used enough
   to want them.

Anything below that line (remaster, personas, LoRA, stems, MIDI) is not the next
round.

## Verification tools

A repaint needs **two** instruments, because the obvious one is blind to the
failure that actually matters:

- `scripts/measure_repaint.py` — is the music intact and did the window change?
  Phase-invariant log-mel; **cannot see lyrics**.
- `scripts/check_repaint_lyrics.py` — are these still the words, in the right
  place? Transcribes the window and compares against the source's own
  transcript.

A window can score 0.83 on the first and be singing a different part of the
song. That is how the first build shipped "verified".

## Sources

- [suno-band-manager STUDIO-EDITOR-REFERENCE.md](https://github.com/zarlor/suno-band-manager/blob/main/src/skills/_shared/references/STUDIO-EDITOR-REFERENCE.md)
- [Suno AI In-Song Editor Guide (v4.5)](https://jackrighteous.com/en-us/blogs/guides-using-suno-ai-music-creation/suno-ai-in-song-editor-v4-replace-extend-crop-more)
- [Enhance AI Music with Suno's Replace Sections](https://jackrighteous.com/en-us/blogs/guides-using-suno-ai-music-creation/replace-sections-ai-music-suno)
- [Suno Editor: How to Edit, Replace, and Rework Sections (2026)](https://undetectr.com/blog/suno-editor)
