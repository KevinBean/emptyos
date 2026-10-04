import importlib.util
from pathlib import Path


SCRIPT = (
    Path(__file__).parents[1]
    / "scripts"
    / "build_routing_review_queue.py"
)
SPEC = importlib.util.spec_from_file_location(
    "build_routing_review_queue", SCRIPT
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_queue_prioritizes_digest_contained_search_review():
    audit = {
        "provider": "chatgpt",
        "records": [
            {
                "provider_id": "legacy",
                "title": "Legacy",
                "new_schema_complete": False,
                "reasons": ["missing-domain-coverage"],
            },
            {
                "provider_id": "contained",
                "title": "Contained",
                "new_schema_complete": False,
                "routing_review_required": True,
                "routing_checks": {"durable_delta_count": 2},
                "reasons": [
                    "delta-digest-contained-routing-evidence-missing:"
                    "Knowledge fragments"
                ],
            },
            {
                "provider_id": "done",
                "title": "Done",
                "new_schema_complete": True,
                "derived_notes": ["30_Resources/KB/done"],
                "reasons": [],
            },
        ],
    }

    queue = MODULE.build_queue(audit, audit_path="audit.json")

    assert queue["total"] == 2
    assert queue["next_review"]["provider_id"] == "contained"
    assert queue["counts"] == {
        "digest-contained-search-review": 1,
        "semantic-routing-review": 1,
    }


def test_non_routing_contract_failure_is_still_backfill():
    audit = {
        "provider": "claude",
        "records": [
            {
                "provider_id": "missing-source",
                "new_schema_complete": False,
                "reasons": ["source-missing-at-expected-path"],
            }
        ],
    }

    queue = MODULE.build_queue(audit, audit_path="audit.json")

    assert queue["items"][0]["review_kind"] == "legacy-evidence-backfill"
