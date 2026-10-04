import importlib.util
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "apply_no_mutation_browser_batch.py"
)
SPEC = importlib.util.spec_from_file_location("browser_batch", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_routing_table_can_point_duplicate_to_existing_digest():
    target = (
        "[[30_Resources/conversations/"
        "2026-07-24-chatgpt-hp-2700-wifi-mi-ma]]"
    )
    text = MODULE.routing_table(
        {
            "delta_dispositions": [
                {
                    "domain": "Knowledge fragments",
                    "disposition": "duplicate-of",
                    "target": target,
                    "reason": "Already captured.",
                }
            ]
        },
        "30_Resources/conversations/current.md",
        [],
    )

    assert target in text
    assert "[[30_Resources/conversations/current]]" not in text


def test_routing_table_defaults_digest_contained_target_to_current_digest():
    text = MODULE.routing_table(
        {
            "delta_dispositions": [
                {
                    "domain": "Knowledge fragments",
                    "disposition": "digest-contained",
                    "reason": "Useful only with source caveats.",
                }
            ]
        },
        "30_Resources/conversations/current.md",
        [],
    )

    assert "[[30_Resources/conversations/current]]" in text


def test_failure_emits_a_parseable_envelope_not_a_traceback():
    """A refusal must reach an agent consumer as data on stdout.

    Refusals were `raise RuntimeError(json.dumps(evidence))`, uncaught by
    main(). The exit code was right, but stdout was empty and stderr held a
    traceback with the JSON embedded mid-message — so `json.loads(stdout)`
    threw and the caller could not read *why* it was refused.
    """
    import json as _json
    import subprocess
    import sys as _sys
    from pathlib import Path as _Path

    script = _Path(__file__).resolve().parents[1] / "scripts" / "apply_no_mutation_browser_batch.py"
    proc = subprocess.run(
        [_sys.executable, str(script), "--capture", "no-such.json", "--spec", "no-such.json"],
        capture_output=True,
        text=True,
    )

    assert proc.returncode != 0
    assert proc.stderr.strip() == "", "diagnostics must not pollute the parsed stream"
    body = _json.loads(proc.stdout)  # must not raise
    assert body["ok"] is False
    assert body["code"] and body["code"] != "ok"
    assert body["message"]


def test_derived_path_must_be_a_markdown_file_inside_the_vault():
    import importlib.util as _il
    from pathlib import Path as _P

    script = _P(__file__).resolve().parents[1] / "scripts" / "apply_no_mutation_browser_batch.py"
    spec = _il.spec_from_file_location("_apply_probe", script)
    mod = _il.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Real paths from the live specs must all pass — an over-tight gate here
    # refuses legitimate work. A folder allowlist was tried and would have
    # wrongly refused 5 of the top-level folders these actually write to.
    for good in (
        "50_Journal/2025/2025-04-09.md",
        "60_Worklogs/2024/week-12.md",
        "30_Resources/University/unisa/completed/course.md",
        "30_Resources/Technology/Methodology/Import-export and mutation transaction contract.md",
    ):
        mod.check_derived_path(good, provider_id="id-x")

    for bad in ("notes/thing.txt", "/etc/passwd.md", "../outside.md"):
        try:
            mod.check_derived_path(bad, provider_id="id-x")
        except ValueError:
            continue
        raise AssertionError(f"should have been refused: {bad}")
