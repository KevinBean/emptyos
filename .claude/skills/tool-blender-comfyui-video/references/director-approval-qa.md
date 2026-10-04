# Director approval and hybrid QA

## Bind the reference that was actually approved

A hybrid render may intentionally replace the source still's background,
grading, geometry, or layered composition. A corruption detector that compares
the final video against that obsolete still can interpret legitimate full-width
roof lines or other architectural edges as colour bands or tearing.

When the approved hybrid is materially different from the source still:

1. bind the exact video bytes with SHA-256;
2. decode a representative frame from that exact video;
3. bind that reference frame with its own SHA-256;
4. use the bound frame for reference-relative corruption and identity checks;
5. retain the original still as provenance, not as the pixel baseline.

In the 2026-07-31 Scene 04 case study, the obsolete-still comparison reported
horizontal tearing at row-edge excess `p99=12.2`, `max=28.9`. The exact same
video compared with its approved decoded first frame measured `p99=0.5`,
`max=1.3`, with no corrupt frame. The video bytes were unchanged.

## Human judgement may settle only the ambiguous floor

Quiet procedural and restrained 2.5D shots can have meaningful motion in a very
small part of the frame. A full-frame or p95 motion detector can report almost
no independent motion even when the director sees coherent leaf motion and
layered parallax at ordinary playback.

An exact, byte-bound `user-director` approval may settle only the
minimum-independent-motion ambiguity. Record the waived finding explicitly.
It must not waive:

- literal freeze or black-frame detection;
- camera shake or excessive global drift;
- excessive local speed or deformation;
- chain-seam discontinuity;
- reference-relative corruption using the correct bound reference;
- identity, anatomy, unexpected-object, or semantic-action failures.

This is a hard-pass / hard-fail / ask boundary: objective failures remain hard
fail; clear technical and artistic success is hard pass; only the ambiguous
minimum-motion judgement is eligible for a constrained user choice.
## Match the detector to the authored motion footprint

Use two temporal evidence channels:

- broad/global motion for water fields, foliage groups, crowds, or camera
  motion;
- coherent sparse/local motion for smoke, hair, moths, one hand, or one plant.

Measure every adjacent frame pair. For the sparse channel, divide the
delivery-scale frame into fixed cells and require both a minimum changed-pixel
fraction and a minimum mean absolute difference inside one cell. Requiring
both rejects isolated codec noise. Continue to track the longest run with
neither broad nor sparse evidence.

Do not change a failed calm shot by blindly increasing motion amplitude. First
decide whether the authored subject is truly static or the detector is
averaging a small moving region into the whole frame. Report the result as:

| Verdict | Meaning | Action |
|---|---|---|
| Hard pass | Objective gates and art intent agree | Lock exact bytes |
| Hard fail | Freeze, corruption, shake, seam, identity, or clear art failure | Repair in the owning stage |
| Ask | Only artistic preference or minimum-motion ambiguity remains | Offer bounded choices plus free-form feedback |

## Lock the clip stage before combination

Before assembly, persist one complete scene-source manifest containing exact
paths, hashes, frame counts, frame rates, dispositions, and review authority.
The assembler must rehash every source and stop on mismatch. Do not scan for a
"latest" filename, inherit approval by path, or reopen approved clips because
an aggregate detector uses a different standard.

After assembly, inspect:

- every cut boundary for repeated frames or one-frame stalls;
- the user-reported timestamps;
- the final audio/video duration and frame count;
- corruption and uniform edge fill;
- motion-rate discontinuity as an advisory, not an automatic reason to
  regenerate a director-locked calm shot.
