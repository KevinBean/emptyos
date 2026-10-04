import argparse
import importlib.util
import json
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "stage_digest_collision_repairs.py"
)
SPEC = importlib.util.spec_from_file_location("stage_collision_repairs", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


PROVIDER_ID = "22222222-2222-4222-8222-222222222222"


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _args(tmp_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        queue=tmp_path / "queue.json",
        spec_dir=tmp_path / "specs",
        spec_glob="reviewed-spec-*.json",
        output=tmp_path / "repair.json",
        repair_date="2026-08-02",
        id=[],
    )


def _setup(tmp_path: Path) -> argparse.Namespace:
    args = _args(tmp_path)
    args.spec_dir.mkdir()
    _write(
        args.queue,
        {
            "provider": "claude",
            "items": [
                {
                    "provider_id": PROVIDER_ID,
                    "reasons": sorted(MODULE.EXPECTED_REASONS),
                }
            ],
        },
    )
    _write(
        args.spec_dir / "reviewed-spec-01.json",
        {
            "provider": "claude",
            "items": [
                {
                    "provider_id": PROVIDER_ID,
                    "ingestion_date": "2026-08-01",
                    "digest": "Reviewed analysis.",
                }
            ],
        },
    )
    return args


def test_build_preserves_reviewed_content_and_enables_narrow_repair(tmp_path):
    args = _setup(tmp_path)

    staged = MODULE.build(args)

    assert staged == [
        {
            "provider_id": PROVIDER_ID,
            "ingestion_date": "2026-08-02",
            "digest": "Reviewed analysis.",
            "reuse_existing_source": True,
            "repair_source_digest_link": True,
        }
    ]
    output = json.loads(args.output.read_text(encoding="utf-8"))
    assert output["record_kind"] == "digest-collision-repair-stage"
    assert output["items"] == staged


def test_build_refuses_unrelated_audit_reasons(tmp_path):
    args = _setup(tmp_path)
    _write(
        args.queue,
        {
            "provider": "claude",
            "items": [
                {
                    "provider_id": PROVIDER_ID,
                    "reasons": ["derived-note-missing"],
                }
            ],
        },
    )

    try:
        MODULE.build(args)
    except ValueError as error:
        assert "Refusing non-collision repair" in str(error)
    else:
        raise AssertionError("Expected unrelated audit reason refusal")


def test_build_refuses_duplicate_reviewed_specs(tmp_path):
    args = _setup(tmp_path)
    _write(
        args.spec_dir / "reviewed-spec-02.json",
        {
            "provider": "claude",
            "items": [{"provider_id": PROVIDER_ID}],
        },
    )

    try:
        MODULE.build(args)
    except ValueError as error:
        assert "Duplicate reviewed spec" in str(error)
    else:
        raise AssertionError("Expected duplicate reviewed spec refusal")
