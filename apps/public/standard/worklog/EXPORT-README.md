## Moving data between the file and EmptyOS

Use **Data → Export JSON** inside either version to create a portable Work Log
file. In EmptyOS, choose **Data → Import JSON** and select that file. You get a
per-day summary of what would change and nothing is written until you press
**Apply import**.

Imports merge by date, project, and item text: new work is added, changed
statuses are updated, and unchanged items are not duplicated. If Plan, Update,
or employer text differs on both sides, EmptyOS keeps the live value and reports
a conflict. Existing day notes are only added to — anything you hand-wrote in
Markdown, including free prose under **Notes**, is left exactly as it was.

The amber offline pill's **Save data to file** backup is also accepted by the
EmptyOS importer, so the browser backup itself can be your handoff file.

AI rollups, smart sentence parsing, PDF generation, CSV export, and app
settings require the EmptyOS daemon. Manual logging, status changes, timeline,
calendar, project, heatmap, **the CPEng tab**, and JSON transfer all work
inside the standalone HTML.

## CPEng competency evidence

Tag any work item with `#c11` to file it against Engineers Australia Stage 2
element 11. The tag is written into the item's own text, so it survives the
markdown notes, the portable JSON, the import merge and this offline file
without any of them needing to know about it — and in a markdown vault it is
also an ordinary searchable tag.

The **CPEng** tab rolls every tagged item up by element, across all sixteen and
all four areas. Elements at zero still render: an element with no evidence is
the finding, not something to hide. Two are marked `gap` — 11 (judgement) and
13 (local engineering knowledge) — because neither can be reconstructed from a
CV afterwards. They have to be caught on the day they happen, which is the
whole reason this works offline: that day happens at the office.
