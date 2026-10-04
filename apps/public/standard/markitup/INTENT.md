# markitup — INTENT

> Living design doc for `apps/public/standard/markitup/`. Edit as the app evolves.
> Birth certificate: the approved session plan (per-machine, under the plans dir).
> Changelog: `10_Projects/emptyos/log/app-development.md`.

## Why

A review of a web page is worth more when each comment points at the thing it
is about. The trigger was a hand-made deliverable: a 20-page annotated PDF of a
client site, produced by a one-off ReportLab script in gitignored scratch that
imported nothing from `emptyos/`. It worked and it was unrepeatable, unreachable
from the daemon, and its pin coordinates were **guessed by the model**.

markitup keeps the shape of that artifact and fixes its one real weakness. The
model never supplies a coordinate. It picks an element from a menu that the
capture measured with `getBoundingClientRect()`, and the pin comes from that
rect. A pin is therefore either exactly right or absent — never plausibly wrong,
which is the failure mode a reader cannot detect.

Two audiences, deliberately: a client's site (where the useful output is *what
would you do about this*) and one of our own surfaces (where it is *which design
property is wrong*). That is why the tag vocabulary is a **rubric table** rather
than a constant.

### What it is not

- **Not a PDF annotator.** Nothing in this repo writes PDF annotation objects
  (`pypdf` is read-only here). A marked-up PDF is *printed* from HTML that
  already carries the pins, through `sdk/pdf.py::render_html_pdf`. No new
  dependency, and the daemon page and the PDF share one geometry.
- **Not `markitdown`.** That is a **plugin** — Microsoft MarkItDown, the local
  document→markdown reader. One letter apart, unrelated. Nothing here touches it.
- **Not `library`.** library annotates *ingested reference papers* with
  citations and highlights. markitup puts visual comments on a *captured
  surface*. Different object, different job.
- **Not a crawler.** Discovery reads one page's same-origin links under a
  budget and proposes; it never wanders and never captures.

## Relationships

Calls into (all via capabilities, never direct tools):

| Reaches | For |
|---|---|
| `browse` (playwright plugin) | navigate, stamp + measure, screenshot |
| `think` | discovery, the review pass, the vision fallback |
| `emptyos/sdk/pipeline.py` | ordered stages, resume, `stop_after` approval gate |
| `emptyos/sdk/run_budget.py` | the per-run cost ceiling |
| `emptyos/sdk/html_anchors.py` | `EDITABLE_TAGS` — the stamped tag set |
| `emptyos/sdk/web_search.py` | `is_public_web_url`, `source_fencer` |
| `emptyos/sdk/flipbook.py` | `build_refine_messages` / `parse_refine_anchors` (L3) |
| `emptyos/sdk/pdf.py` | `render_html_pdf` |

Emits `markitup:review_started`, `markitup:review_ready`,
`markitup:comment_resolved` (internal — the UI is the only consumer) and
`markitup:exported` (public, so a reactor can breadcrumb a delivered review).

Declares no `[requires] apps` — it depends on no other app, which is what lets
it ship in `standard` without dragging anything else in.

## Open questions

- **Is one pin per comment enough?** A comment about the relationship between
  two elements currently pins to one of them. A leader line between two anchors
  is the obvious answer and was deliberately deferred (designer made the same
  call: pins, not leaders).
- **Should a review live in the vault rather than `data/`?** Working state is
  machine state and the screenshots are large, so it is in `data/` today with the
  deliverable exported to the vault. If reviews start being long-lived records
  the user edits over weeks, that split may be the wrong one.
- **Does the `design` rubric actually earn its keep here**, given
  `eos-page-design-review` already reviews our own pages from source? The bet is
  that a rendered, pinned review catches what a source read cannot. Unproven.

## Sources — the shot-producer seam (2026-09-10)

A review is ordered shots plus normalised-coordinate comments, and the only
medium-specific part is how a **source** becomes shots. That is now a seam
(`sources.py`): `source = {kind, ref, title?}` with `kind` one of `web`,
`document`, `pdf`, `image`, and one producer per kind that returns shot
records. The capture stage dispatches and knows nothing about kinds; the
review pass, pins, statuses and exports read shots and sidecars exactly as
before. `views[{url}]` is still accepted and read as `kind: web` — on purpose,
and undocumented.

Two producers are live. **`web`** is the original browser capture.
**`document`** renders a markdown file from an allow-listed root (`docs/` in
the repo, `30_Resources/` in the vault, this app's `uploads/`) to HTML at
`GET /api/render/{root}/{path}`, then reuses the browser capture on that
own-daemon URL. Going through the browser is deliberate: `browse` cannot load
raw HTML, the reviewer reads the very page the capture measured, and the
own-daemon gate plus the deep-link sign-in apply unchanged. The rendered page
stamps `id` on every table row and heading that opens with a requirement-style
identifier (`SR-INP-06`, `ALG-GEN-FIRM-01`), carries the file's content hash
in its header so every screenshot is version-bound, and asks the capture for
**row-level** anchors — a `<tr>` has no direct text of its own, so the default
script could never anchor one, and a requirements spec is reviewed row by row.
Measured on the first live run: 20 anchors and zero rows before the switch,
132 anchors with 85 id-leading rows after.

`pdf` and `image` are registered so the vocabulary is complete and refuse with
a reason until their producers land (plan `markitup-source-adapters`, T5 —
gated on the rasteriser licence decision).

## Future

- **PDF and image producers.** The seam is proven; PDF needs a rasteriser
  (PyMuPDF is AGPL, pypdfium2 permissive — a licence decision, not a build
  one) and yields measured anchors from the text layer; an image is one shot
  with no anchors, pinned by hand or by the vision fallback.
- ~~Click-to-comment in the UI, and showing a comment's `ref` in the gutter~~ —
  landed as T3 (2026-10-02). A click on empty canvas asks
  `GET /api/reviews/{rid}/shots/{sid}/snap?x=&y=` which measured element the
  point means (`shared.snap_to_anchor`: the smallest box containing it, else
  the nearest within `SNAP_MAX_PX` page pixels), shows it in the dialog before
  the user types, and `POST …/comments` with `snap: true` pins there through
  `pin_to_anchor` — `anchor: dom`, `el` and `ref` recorded, the same
  `rect_to_point` a model placement uses. Nothing within reach → the comment
  lands with no pin, never an estimate. The new-review dialog offers a URL or a
  document under an allow-listed root, with `GET /api/documents?root=` feeding
  a datalist of markdown files. A pin *dragged* by hand still becomes a human
  placement and drops its `el`/`ref` (a pin dragged off the `SR-INP-06` row
  must not keep saying `SR-INP-06`). (The `requirements` rubric, the `ref` derived at
  review time from the measured anchor text of a **document** shot, and
  per-kind source lines in the exports landed as T2: a model-placed pin on the
  `SR-INP-06` row carries `SR-INP-06` whatever the model wrote in the title,
  and a document shot's export prints `Document: <ref> (sha256 …)` rather than
  the loopback render URL. Web shots never get a `ref` — the id matcher is a
  generic hyphen-number shape and a heading like "COVID-19 response" would
  otherwise export as a requirement.)
- Re-review: capture the same shot list again and diff the comments, so "did
  they fix it" is a question the app can answer.
- A `[provides.timeline]` entry once a review has a history worth showing.
