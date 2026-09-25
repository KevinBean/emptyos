import importlib.util
from pathlib import Path


SCRIPT = (
    Path(__file__).parents[1]
    / "scripts"
    / "audit_evidence_graph.py"
)
SPEC = importlib.util.spec_from_file_location(
    "audit_chatgpt_identity_alias", SCRIPT
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_explicit_alias_accepts_chatgpt_version_8_style_ids():
    superseded = "69ccb832-da30-839a-af94-3ea9bc8d875c"
    canonical = "69ccb832-da30-839a-833f-87b2fad1253b"
    ledger = (
        f"- provider-identity-alias · superseded-provider-id {superseded} · "
        f"canonical-provider-id {canonical} · not a second conversation"
    )

    assert MODULE.superseded_provider_ids(ledger) == {superseded}


def test_version_8_style_id_without_alias_receipt_is_not_superseded():
    provider_id = "69ccb832-da30-839a-af94-3ea9bc8d875c"

    assert MODULE.superseded_provider_ids(
        f"- historical row only · {provider_id}"
    ) == set()
