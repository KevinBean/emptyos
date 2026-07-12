"""Phase 1 — autopilot floor derives from the verb registry (drift-killer).

Proves the injected-set model: when an effective eligible set is passed as
``eligible=``, it is authoritative, and a ``stable -> gated`` flip (the verb
leaving the set) revokes auto-apply even though the grant still exists.

Pure — tmp_path data_dir, no daemon. Run:
    python -m pytest tests/test_unit_autopilot_registry_floor.py -v
"""

from __future__ import annotations

import json

import pytest

from emptyos.sdk.autopilot import (
    _operator_overrides,
    effective_eligible,
    is_eligible,
    match,
    save_grant,
)


@pytest.fixture
def data_dir(tmp_path):
    return tmp_path


def _write_policy(data_dir, **keys):
    root = data_dir / "autopilot"
    root.mkdir(parents=True, exist_ok=True)
    (root / "policy.json").write_text(json.dumps(keys), encoding="utf-8")


# ── effective_eligible: (derived ∪ operator_eligible) − operator_removed ─────

def test_effective_is_derived_when_no_operator_overrides(data_dir):
    assert effective_eligible(data_dir, {"task.add", "kb.tag"}) == {"task.add", "kb.tag"}


def test_operator_eligible_adds(data_dir):
    _write_policy(data_dir, operator_eligible=["custom.verb"])
    assert effective_eligible(data_dir, {"task.add"}) == {"task.add", "custom.verb"}


def test_operator_removed_subtracts_even_from_derived(data_dir):
    # Operator pulled task.add; the registry re-adds it; subtraction wins.
    _write_policy(data_dir, operator_removed=["task.add"])
    assert effective_eligible(data_dir, {"task.add", "kb.tag"}) == {"kb.tag"}


def test_operator_overrides_reads_both_keys(data_dir):
    _write_policy(data_dir, operator_eligible=["a.b"], operator_removed=["c.d"])
    op_el, op_rm = _operator_overrides(data_dir)
    assert op_el == {"a.b"}
    assert op_rm == {"c.d"}


# ── is_eligible: injected set is authoritative ───────────────────────────────

def test_is_eligible_uses_injected_set(data_dir):
    assert is_eligible(data_dir, "task.add", eligible={"task.add"})
    assert not is_eligible(data_dir, "task.add", eligible={"kb.tag"})


def test_is_eligible_injected_empty_set_blocks_everything(data_dir):
    # An empty injected set is NOT the same as None — it means "nothing eligible".
    assert not is_eligible(data_dir, "task.add", eligible=set())


def test_is_eligible_none_falls_back_to_legacy_policy(data_dir):
    # eligible=None preserves today's behaviour: read policy.json eligible_verbs.
    # Note load_policy() auto-merges DEFAULT_ELIGIBLE_VERBS, so task.add (a
    # default) is eligible; a verb that's neither a default nor written is not.
    _write_policy(data_dir, eligible_verbs=["task.add"])
    assert is_eligible(data_dir, "task.add")  # eligible defaults to None
    assert not is_eligible(data_dir, "madeup.verb")


# ── the revocation scenario (the whole point) ────────────────────────────────

def test_stable_to_gated_flip_revokes_match(data_dir):
    """A grant covering task.add auto-applies while task.add is in the floor;
    the moment the floor no longer contains it (eligibility flipped to gated in
    the manifest -> the derived set drops it), match() returns None even though
    the grant is untouched. This is the drift-killer.
    """
    floor_before = {"task.add"}
    g = save_grant(
        data_dir,
        actor_type="cli",
        actor_id="claude-cli",
        verb_pattern="task.add",
        scope="room:r1",
        eligible=floor_before,
    )
    assert g["id"]

    # While eligible: the grant matches and would auto-apply.
    hit = match(
        data_dir, actor_type="cli", actor_id="claude-cli",
        verb="task.add", scope_candidates=["room:r1"], eligible=floor_before,
    )
    assert hit is not None and hit["id"] == g["id"]

    # Flip: task.add left the registry floor (stable -> gated). Grant unchanged.
    floor_after: set[str] = set()
    miss = match(
        data_dir, actor_type="cli", actor_id="claude-cli",
        verb="task.add", scope_candidates=["room:r1"], eligible=floor_after,
    )
    assert miss is None  # revoked purely by the floor shrinking


def test_save_grant_allows_registry_only_verb_via_injected_floor(data_dir):
    # A verb NOT in legacy policy.json but present in the injected registry floor
    # must be grantable (save-time check agrees with fire-time check).
    g = save_grant(
        data_dir,
        actor_type="mcp-client",
        actor_id="codex",
        verb_pattern="newapp.do_thing",
        scope="mcp:codex",
        eligible={"newapp.do_thing"},
    )
    assert g["id"]


def test_save_grant_rejects_verb_outside_injected_floor(data_dir):
    with pytest.raises(ValueError, match="not autopilot-eligible"):
        save_grant(
            data_dir,
            actor_type="mcp-client",
            actor_id="codex",
            verb_pattern="rooms.write_note",
            scope="mcp:codex",
            eligible={"task.add"},  # write_note not in floor
        )


def test_save_grant_glob_requires_injected_floor_match(data_dir):
    # Namespace glob must match at least one verb in the injected floor.
    with pytest.raises(ValueError, match="matches no eligible verbs"):
        save_grant(
            data_dir, actor_type="voice", actor_id="aura",
            verb_pattern="empty.*", scope="global", eligible={"task.add"},
        )
    # And succeeds when the floor has a matching namespace.
    g = save_grant(
        data_dir, actor_type="voice", actor_id="aura",
        verb_pattern="task.*", scope="global", eligible={"task.add"},
    )
    assert g["id"]
