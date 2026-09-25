"""The technical-report -> verified-calculator handoff is executable."""

from __future__ import annotations

import asyncio
import tomllib

from helpers import app_path, load_app_module

GRILL = load_app_module("grill", "app")
RUNS = load_app_module("app-builder", "runs", preload=("constants", "prompts"))
PROMPTS = load_app_module("app-builder", "prompts")


def _answers(**overrides):
    values = {
        "app_id": "fault-current-demo",
        "app_name": "Fault Current Demo",
        "endpoint_verb": "fault_current",
        "engine_id": "fault-current-demo",
        "data_shape": "stateless calculator",
    }
    values.update(overrides)
    return values


def test_recipe_collects_stable_handoff_ids_separately():
    recipe_path = app_path("grill") / "recipes" / "engineering-calculator.toml"
    recipe = tomllib.loads(recipe_path.read_text(encoding="utf-8"))
    keys = [q["key"] for q in recipe["questions"]]
    assert "engine_id" in keys
    assert keys[-2:] == ["app_id", "app_name"]
    assert "name" not in keys


def test_calculator_frontmatter_is_deterministic_and_app_builder_ready():
    frontmatter, errors = GRILL._engineering_handoff_frontmatter(_answers())
    assert errors == []
    assert frontmatter == {
        "app_id": "fault-current-demo",
        "app_name": "Fault Current Demo",
        "verb": "fault_current",
        "engine_id": "fault-current-demo",
        "capabilities": [],
        "surfaces": ["custom-page"],
        "data_shape": "stateless",
    }


def test_write_spec_persists_the_handoff_frontmatter():
    class FakeGrill:
        captured = None

        def vault_config(self, key, default):
            return default

        def vault_create_note(self, path, frontmatter, body):
            self.captured = (path, frontmatter, body)

    fake = FakeGrill()
    extra, errors = GRILL._engineering_handoff_frontmatter(_answers())
    assert errors == []
    path = asyncio.run(
        GRILL.GrillApp._write_spec(
            fake,
            "engineering-calculator",
            "## Why\nA checked report is ready to become executable.",
            ["### Source\nA published worked example."],
            extra_frontmatter=extra,
        )
    )
    saved_path, frontmatter, body = fake.captured
    assert path == saved_path
    assert frontmatter["recipe"] == "engineering-calculator"
    assert frontmatter["app_id"] == "fault-current-demo"
    assert frontmatter["engine_id"] == "fault-current-demo"
    assert "## Raw answers" in body


def test_calculator_frontmatter_refuses_missing_or_unsafe_boundaries():
    frontmatter, errors = GRILL._engineering_handoff_frontmatter(
        _answers(app_id="../escape", engine_id="", endpoint_verb="Fault Current")
    )
    assert frontmatter == {}
    assert errors == [
        "app_id must be a kebab-case id",
        "endpoint_verb must be a snake_case id",
        "engine_id must be a kebab-case id",
    ]


def test_calculator_frontmatter_preserves_study_data_contract():
    frontmatter, errors = GRILL._engineering_handoff_frontmatter(
        _answers(data_shape="owns projects/studies")
    )
    assert errors == []
    assert frontmatter["data_shape"] == "vault-frontmatter"
    assert frontmatter["capabilities"] == ["read", "write"]

    frontmatter, errors = GRILL._engineering_handoff_frontmatter(
        _answers(data_shape="something improvised")
    )
    assert frontmatter == {}
    assert errors == ["data_shape must be selected from the recipe"]


def test_engineering_scaffold_scope_includes_only_its_app_engine_and_tests():
    prefixes = RUNS._scaffold_allowed_prefixes(
        {
            "recipe": "engineering-calculator",
            "app_id": "fault-current-demo",
            "engine_id": "fault-current-demo",
        }
    )
    assert "apps/extension/engineering/fault-current-demo/" in prefixes
    assert "engines/fault-current-demo/" in prefixes
    assert "tests/test_sys_fault_current_demo.py" in prefixes
    assert not any(p.startswith("emptyos/") for p in prefixes)
    assert not any(p == "apps/fault-current-demo/" for p in prefixes)


def test_normal_scaffold_scope_does_not_gain_engine_access():
    prefixes = RUNS._scaffold_allowed_prefixes(
        {
            "recipe": "new-app",
            "app_id": "reading-list",
            "engine_id": "should-not-escape",
        }
    )
    assert "apps/reading-list/" in prefixes
    assert not any(p.startswith("engines/") for p in prefixes)


def test_recipe_selects_the_calculator_prompt_without_affecting_overrides():
    selected = RUNS._scaffold_system_prompt({"recipe": "engineering-calculator"})
    assert selected is PROMPTS._ENGINEERING_CALCULATOR_BUILDER_SYSTEM_PROMPT
    assert RUNS._scaffold_system_prompt({"recipe": "new-app"}) is PROMPTS._BUILDER_SYSTEM_PROMPT
    assert (
        RUNS._scaffold_system_prompt({"recipe": "engineering-calculator"}, "explicit override")
        == "explicit override"
    )


def test_calculator_builder_prompt_preserves_the_evidence_contract():
    prompt = PROMPTS._ENGINEERING_CALCULATOR_BUILDER_SYSTEM_PROMPT
    for required in (
        "CalculatorRoutesMixin",
        'self.engine("<engine_id>")',
        "published reference case",
        "Do not move a tolerance",
        "model calls in the mathematics",
        "apps/extension/engineering/<app_id>/",
    ):
        assert required in prompt


def test_recipe_captures_the_trust_loop_handoffs():
    recipe_path = app_path("grill") / "recipes" / "engineering-calculator.toml"
    recipe = tomllib.loads(recipe_path.read_text(encoding="utf-8"))
    keys = [q["key"] for q in recipe["questions"]]
    for key in (
        "engineering_claim",
        "source_evidence_status",
        "report_deliverable",
        "end_to_end_journey",
    ):
        assert key in keys


def test_calculator_builder_scaffolds_the_assurance_package_as_draft():
    prompt = PROMPTS._ENGINEERING_CALCULATOR_BUILDER_SYSTEM_PROMPT
    for filename in (
        "ASSURANCE.md", "SOURCE-PACK.md", "ALGORITHM.md", "IMPLEMENTATION.md",
        "VALIDATION.md", "REPORT-SPEC.md", "RELEASE-VERIFICATION.md",
    ):
        assert filename in prompt
    assert 'status = "draft"' in prompt
    assert "only generated receipts can promote it" in prompt
