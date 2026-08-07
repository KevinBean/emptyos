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

AI rollups, smart sentence parsing, PDF generation, and app settings require
the EmptyOS daemon. Manual logging, status changes, timeline, calendar, project,
heatmap, and JSON transfer all work inside the standalone HTML.
