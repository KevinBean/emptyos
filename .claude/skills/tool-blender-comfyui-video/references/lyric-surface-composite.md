# Real lyrics on a generated page (Blender surface composite)

Case: 不可說 / The Unsaid, shots U019 (two-page spread) and U020 (manuscript
close-up), 2026-09-14. This records what was actually built and where it
stops working. It is not a finished-art preset.

## Why this exists

Generated paper carries fake glyphs, and image-to-video models cannot write
real text. Laying a subtitle over the page produces double text. The fix is to
clean the page and put the song's real lyric onto it as geometry in the page's
perspective.

## Pipeline that shipped

1. **Clean plate.** Edit the generated still to remove the fake writing while
   keeping paper texture and light (built-in image edit →
   `U020-clean-page-v1.png`, `U019-clean-spread-v1.png`).
2. **Headless Blender build** — one script per shot, re-runnable:
   `blender.exe -b -t 4 --python build_<shot>.py`. Blender 5.0; about 5–10 s
   per render.
   - Orthographic camera over an **emission** plane textured with the clean
     plate. Use view transform `Standard` with no look, exposure 0, so the
     plate's colours pass through unchanged. Cycles at 8 samples is enough for
     an emission-only scene.
   - **Perspective by homography, not by eye.** Solve the 3×3 homography from
     the paper's unit square to the four observed page corners in plate
     pixels. Convert each text object to a mesh and move every vertex through
     that homography onto the plate. Text follows the page's perspective and
     stays editable geometry. For a two-page spread, use one homography per
     page.
   - **Read lyrics from the release subtitle file, never retype them.** Assert
     the line count (36 here), so a missing or duplicated line fails the build.
   - Font: KaiTi (`simkai.ttf`, a Windows system font). Ink: a dark warm
     emission colour (≈0.075, 0.055, 0.039), not pure black.
   - Store review state on the scene (`review_status`, `lyric_timeline`) and
     `pack_all()` before saving the `.blend`.
3. **Motion without animating text.** The page itself does not move in these
   shots, so the static Blender render is composited over the Flow motion clip
   with an ffmpeg `blend` and an analytic soft mask.
   - Close-up: left of a slanted line = rendered manuscript; right = the moving
     clip (20 px feather).
   - Spread: the paper quadrilateral (four half-planes, 12 px feather) takes
     the exact manuscript; the table perimeter outside it keeps the moving
     light.

   This was needed because the raw Flow clip of the written spread spilled
   moving light across the paper, which smeared the text.

## Layout corrections Kevin made

| Version | Result | Kevin's note | Change |
|---|---|---|---|
| v1 | Two lines on one page | Write the whole lyric | All 36 lines across the spread, first half on each page |
| v2 | 36 single lines at small height in a narrow left column | 紙面太空 (page looks empty) | Join adjacent lines into couplets with a full-width space; text height 0.030 → 0.046 of page; width capped at 0.77 |
| v3/v4 | Filled but uniform block | 要有美感 (make it beautiful) | Group rows in threes with an extra gap (`vtop = .22 + row*.068 + (row//3)*.028`); spread and close-up share the same couplets so the close-up is legibly part of the spread |

## Limits — do not reuse blindly

- **Locked-off paper only.** A moving camera, page turn or curl needs planar
  tracking and deformation. See `docs/BLENDER-ANIMATION-GUIDE.md` §4 for
  surface attachment and paper peel.
- **Text is not re-lit.** Emission ink ignores scene light. The mask keeps
  moving reflections off the text rather than making them pass over it. A shot
  where light must sweep across the writing needs a lit material instead.
- **Hard-coded pixel mapping.** The corners and the `px/1672*16-8` mapping
  belong to that plate's native size (1672×941). Re-measure for every new
  plate.
- **Font licensing.** `pack_all()` embeds the system font in the `.blend`.
  Keep the file private, or confirm the licence before sharing the project.
- **Review at the time was** stills plus sampled motion frames, not a
  full-speed review of the shot. The v8–v11 deliveries later carried it
  without a reported defect.

## Checks

- Render the page at delivery size and read every line against the lyric file.
- Compare spread and close-up: same words, same line breaks, same hand.
- Inspect a middle frame of the composite for mask edges and light crossing
  the text, not only the first and last frames.
- Keep the `.blend`, the build script, the clean plate and its source image
  together (here `post-production-v8/` plus `tmp/unsaid-mv-post/build_*.py`).
