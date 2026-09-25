import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "claude_export_queue.py"
SPEC = importlib.util.spec_from_file_location(
    "provider_scoping_followup_rows", SCRIPT
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_providerless_followup_does_not_leak_explicit_chatgpt_id_into_claude():
    provider_id = "69e55188-4b60-8322-8b08-5024bba2e519"
    ledger = (
        f"- 2026-07-24 · chatgpt-older-gap · {provider_id} · complete\n"
        f"- 2026-07-24 · derived-route · {provider_id} · reused-no-change\n"
    )

    assert MODULE.processed_ids_from_ledger(
        ledger, provider="chatgpt"
    ) == {provider_id}
    assert MODULE.processed_ids_from_ledger(
        ledger, provider="claude"
    ) == set()


def test_truly_providerless_legacy_row_remains_eligible_for_compatibility():
    provider_id = "11111111-1111-4111-8111-111111111111"
    ledger = f"- 2024-01-01 · legacy-import · {provider_id} · complete\n"

    assert MODULE.processed_ids_from_ledger(
        ledger, provider="claude"
    ) == {provider_id}
    assert MODULE.processed_ids_from_ledger(
        ledger, provider="chatgpt"
    ) == {provider_id}
