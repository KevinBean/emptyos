## Planner round-trip (the team sync)

Microsoft Planner stays the IT-approved system of record; this board is the
richer local editor.

1. In Planner: **Export plan to Excel** → you get an .xlsx.
2. In Boards: **⇪ Planner** → pick the file → check the column mapping →
   review the dry-run diff (nothing is written yet) → **Import**.
3. Work in the board: kanban by bucket, filters, comments, checklists,
   due dates.
4. **⇩ .xlsx** exports the board back in the same Planner column layout for
   sharing or Power Automate consumption. (Planner itself has no bulk
   re-import; the export is for humans and downstream tooling.)

Re-importing a newer Planner export updates existing tasks by Task ID —
no duplicates.
