---
name: eos-tutorial-verify
description: Verify that a generated tutorial series actually works by following it end to end as a reader would, in a fresh scratch directory, and recording verified/skipped/failed onto the learn course. Use when the user says "/eos-tutorial-verify <course-id>", "verify the tutorial", or "does that tutorial actually run". Read-only on the tutorial notes — never edits them to make them pass. NOT for verifying code changes in this repo (use /verify) or running the test suite.
---

# EOS Tutorial Verify — follow it like a reader

Follow a generated tutorial series (`/eos-tutorial`) exactly as a reader would,
in a throwaway directory, and record whether it actually works. Borrowed from
[devenjarvis/lathe](https://github.com/devenjarvis/lathe)'s `/lathe-verify`.

Isolation is **by instruction** — a fresh temp dir under normal permissions.
No Docker, no sandbox-exec. This skill is **read-only with respect to the
tutorial**: never edit part notes or the course note directly. The only state
writes go through the learn API.

## Protocol

All API calls: `127.0.0.1:9000` (never `localhost`), bearer token from
`emptyos.toml [network] auth_token`, Python urllib for any non-ASCII body.
The endpoint is dark-flagged — if it returns `{"error": "disabled"}`, tell the
user to flip `learn.feature.tutorial.enabled` in Settings and stop.

1. **Mark it in-flight first:**
   ```
   POST /learn/api/tutorial/verify-result  {"course_id": "<id>", "status": "verifying"}
   ```
   Status is set by *this skill*, never by a web button — so an unclicked
   button can never strand a course at `verifying`.

2. **Make a fresh scratch dir and work there.** e.g. PowerShell
   `New-Item -ItemType Directory ([System.IO.Path]::GetTempPath() + "eos-verify-" + (Get-Random))`
   or `mktemp -d` in bash. Everything the tutorial tells the reader to create
   happens here — never in the user's projects or the EmptyOS repo.

3. **Follow each part in order.** Get the lesson list from
   `GET /learn/api/courses/<id>`, read each part note from the vault
   (`30_Resources/EmptyOS/kb/notes/tutorial-<series>-part-NN.md`), from part 01
   up. Install prerequisites, create the files, paste the code blocks exactly
   as written, in order, then run each `## Checkpoint` command and compare
   against the stated expected output.
   - **Skip the pedagogical callouts** — `> [!PREDICT]`, `> [!RECALL]`,
     `> [!UNVERIFIED]` prompt the reader or flag uncertainty; nothing to execute.
   - The Checkpoint commands + code blocks are the executable surface.

4. **Record the terminal result:**
   - Everything works → `{"course_id": …, "status": "verified"}`
   - A required toolchain isn't installed → `{"course_id": …, "status": "skipped"}`
     — **not a failure**; it means "couldn't run it here", not "the tutorial is
     wrong". Use whenever the compiler/runtime/SDK the tutorial needs is missing.
   - Something genuinely breaks (wrong output, code doesn't compile, a step
     contradicts itself) →
     ```json
     {"course_id": "…", "status": "failed",
      "part": "part-02", "failed_step": 3,
      "error": "<the error message or mismatched output>"}
     ```

5. **Clean up the scratch dir** (it's yours — you created it), then **report
   to the user**: verified clean, skipped (which tool was missing), or where
   exactly it failed — and for a failure, suggest `/eos-tutorial extend` is NOT
   the fix; the broken part should be regenerated or hand-edited by the user.

## Boundaries

- **Read-only on the tutorial.** Never edit a part note, the course note, or
  its frontmatter directly — the only state write is the verify-result call.
  Never "fix" the tutorial so it passes; a failure is the finding.
- **Skipped ≠ failed.** Missing toolchain → `skipped`. Reserve `failed` for the
  tutorial being genuinely broken.
- **Scratch-dir only.** If a step would write outside the scratch dir (global
  installs excepted, and ask before any global install), stop and report.
- **Engineering (non-executable) tutorials** — hand-calculation or
  standards-study series with no runnable checkpoints: work the numeric
  checkpoints by hand/Python in the scratch dir and compare stated values; if
  a part has no checkable claims at all, that itself is a `failed` finding
  ("part N has no verifiable checkpoint").
