r"""Mutation table for tests/test_unit_storage_scan_args.py.

Committed so the "red-proven" claim in the eos-storage-cleanup SKILL.md is
RE-RUNNABLE rather than asserted. A hostile review on 2026-09-23 flagged the
bare claim as unsupported, and it was right to: nothing in the repo recorded
which behaviours had been proven, and one of the original six reds turned out
to be a textual artifact rather than a behavioural pin (see M1 below).

Run:
    python .claude/skills/eos-mutation-verify/run_mutations.py \
        tests/mutations/storage_scan_args.py

Windows only -- the tests skip on any other platform, so every row would
report SURVIVED elsewhere.
"""

TARGET = ".claude/skills/eos-storage-cleanup/scan_storage.ps1"
TESTS = "tests/test_unit_storage_scan_args.py"

MUTATIONS = [
    # --- defect 3: the reserve enumeration -------------------------------
    # M1 is the one that matters. The PREVIOUS version of the test grepped the
    # source for "Get-ChildItem", which the explanatory comment above the code
    # satisfies on its own -- so deleting the pipeline entirely left the suite
    # green while the scanner silently stopped reporting pagefile/hiberfil,
    # i.e. the exact 74.6 GB false negative the fix exists to close.
    ("M1 reserve pipeline deleted, comment left in place",
     '''    Get-ChildItem -LiteralPath "${DriveName}:\\" -File -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^(pagefile|hiberfil|swapfile)\\.sys$' } |
        Sort-Object Name''',
     '''    @()''',
     "reserve_files_are_found_by_executing"),

    # M2: the `gi` alias -- a textual grep for "Get-Item" would not even see
    # this one, and a grep for "Get-ChildItem" stays satisfied by the comment.
    ("M2 reserve enumeration replaced by the Get-Item alias",
     '''    Get-ChildItem -LiteralPath "${DriveName}:\\" -File -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^(pagefile|hiberfil|swapfile)\\.sys$' } |
        Sort-Object Name''',
     '''    gi -LiteralPath "${DriveName}:\\pagefile.sys" -Force -ErrorAction SilentlyContinue''',
     "reserve_files_are_found_by_executing"),

    # --- defect 1: the comma split ---------------------------------------
    ("M4 comma-joined -Drives string is not split at all",
     "foreach ($part in ([string]$spec -split ',')) {",
     "foreach ($part in @([string]$spec)) {",
     "comma_string_from_file_invocation"),

    # The shape the old single-drive assertion could not see: the split runs
    # but only its first element survives, so "C,D" silently scans C alone.
    ("M5 comma split keeps only the first element",
     "foreach ($part in ([string]$spec -split ',')) {",
     "foreach ($part in @(([string]$spec -split ',')[0])) {",
     "comma_string_from_file_invocation"),

    ("M6 colon is not stripped from a drive spec",
     '$n = $part.Trim().Trim("\\").Trim(":").Trim()',
     '$n = $part.Trim().Trim("\\").Trim()',
     "drive_spec_is_normalised"),

    ("M7 drive match becomes case-sensitive",
     '$all = @($all | Where-Object { $wanted -contains $_.Name })',
     '$all = @($all | Where-Object { $wanted -ccontains $_.Name })',
     "lowercase_spec_matches_uppercase_drive"),

    # --- defect 2: the aborts --------------------------------------------
    # The live bypass a hostile review found: an empty resolve read as "no
    # filter requested", so `-Drives ","` scanned EVERY drive at exit 0.
    ("M8 supplied-but-empty -Drives no longer aborts",
     'if ($Drives.Count -gt 0 -and $wanted.Count -eq 0) {',
     'if ($false) {',
     "drives_supplied_but_naming_nothing"),

    ("M9 unresolvable drive set no longer aborts",
     'if ($all.Count -eq 0) {',
     'if ($false) {',
     "absent_drive_letter_aborts_with_exit_2"),

    # Exit code is the documented contract, not just "non-zero".
    ("M10 abort exits 1 instead of the documented 2",
     '''    [Console]::Error.WriteLine("scan_storage: nothing to scan ($why). Aborting rather than writing an empty report.")
    exit 2''',
     '''    [Console]::Error.WriteLine("scan_storage: nothing to scan ($why). Aborting rather than writing an empty report.")
    exit 1''',
     "absent_drive_letter_aborts_with_exit_2"),

    # --- diagnostic-switch invariants ------------------------------------
    ("M11 ListDrivesOnly no longer precedes the report header",
     'if ($ListDrivesOnly) {\n    foreach ($d in $all) { Write-Output $d.Name }\n    exit 0\n}',
     'if ($ListDrivesOnly -and $false) {\n    foreach ($d in $all) { Write-Output $d.Name }\n    exit 0\n}',
     "single_drive_resolves_to_exactly_that_drive"),

    ("M12 ListReservesOnly falls through into the full scan",
     'if ($ListReservesOnly) {',
     'if ($ListReservesOnly -and $false) {',
     "diagnostic_switches_write_nothing"),
]

# NOT PINNED, and deliberately recorded rather than quietly omitted. Both are
# environment-dependent, which is exactly the kind of gap that gets silently
# dropped from a "all red-proven" claim:
#
#   1. The `-and $fixed -contains $_.Name` clause (which excludes mapped network
#      and SUBST drives so a default run cannot -Recurse a network share) cannot
#      be mutation-proven on a machine with no mapped drive -- with none
#      attached, removing the clause changes nothing observable. Pinning it needs
#      a SUBST fixture (`subst X: C:\some\dir`), which mutates machine state and
#      is not something a test should do unattended.
#
#   2. test_reserve_listing_is_confined_to_the_named_drive has no mutation,
#      because confinement is only observable when TWO drives carry reserve
#      files. On the box this was written on, D:\pagefile.sys had just been
#      deleted as an orphan, leaving C: as the only drive with any -- so a
#      mutation that ignores the filter and lists every drive produces
#      byte-identical output. The test is still worth keeping (it will bite on a
#      machine with a second pagefile), but it is NOT proven here and must not
#      be counted as such.
