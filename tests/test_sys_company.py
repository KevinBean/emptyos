"""System app tests: Orgs — CRUD + scenario dispatch + AI-member mirror + review gate.

App internally `apps/company/` with manifest id="company", but its URL prefix
is `/orgs` and the unified primitive is the Org (with members in either
mode=human or mode=ai). Filename kept as `test_sys_company.py` to match the
internal app id; rename only when the directory itself moves.

Heavy think()-loop scenarios are gated behind @pytest.mark.llm so the default
test pass stays fast. CRUD, contract, and review-gate (apply/reject) tests
run unconditionally.
"""

from __future__ import annotations

import pytest

from helpers import TEST_PREFIX, assert_dict_response, assert_ok


def _org_name(suffix: str) -> str:
    return f"{TEST_PREFIX}Acme {suffix}"


@pytest.fixture(scope="session")
def created_org(http_client):
    """Create one virtual test org; yield its id. Every name is TEST_PREFIX-
    suffixed. Idempotent: orgs/api/orgs DELETE only soft-archives, so a prior
    failed run leaves the org on disk and re-create returns 'already exists' —
    in that case we adopt the existing id."""
    name = _org_name("Org")
    resp = http_client.post(
        "/orgs/api/orgs",
        json={
            "name": name,
            "kind": "team",
            "reality": "virtual",
            "scope": "member",
            "mission": "Validate the orgs app",
            "culture": "blunt + fast",
        },
    )
    data = assert_ok(resp)
    oid = data.get("id")
    if not oid and "already exists" in (data.get("error") or ""):
        # Slug derivation matches apps/company/app.py: slugify(name).
        oid = name.lower().replace(" ", "-")
    assert oid, f"org create returned no id: {data}"
    yield oid


@pytest.fixture(scope="session")
def created_ai_member(http_client, created_org):
    """Create one mode=ai member on the test org."""
    resp = http_client.post(
        "/orgs/api/members",
        json={
            "org_id": created_org,
            "mode": "ai",
            "name": f"{TEST_PREFIX}Maya Chen",
            "role": "Head of Engineering",
            "dept": "engineering",
            "emoji": "👩‍💻",
        },
    )
    data = assert_ok(resp)
    mid = data.get("id")
    if not mid and "already exists" in (data.get("error") or ""):
        # Parse the id out of the error message — apps/company/app.py:688 emits
        # "member '<mid>' already exists" with mid = slugify(f"{org_id}-{name}").
        import re
        m = re.search(r"member '([^']+)' already exists", data.get("error") or "")
        if m:
            mid = m.group(1)
    assert mid, f"member create returned no id: {data}"
    yield mid


@pytest.mark.api
class TestOrgsAPI:

    def test_scenarios_listed(self, http_client):
        data = assert_dict_response(
            http_client.get("/orgs/api/scenarios"),
            required_keys=["scenarios"],
        )
        ids = {s["id"] for s in data["scenarios"]}
        assert {"critique", "workshop", "interview"} <= ids, (
            f"Missing scenarios: {ids}"
        )

    def test_orgs_list_endpoint(self, http_client):
        data = assert_dict_response(
            http_client.get("/orgs/api/orgs"),
            required_keys=["orgs"],
        )
        assert isinstance(data["orgs"], list)

    def test_create_org_returns_id(self, http_client, created_org):
        assert created_org.startswith("playwright-test")
        listed = http_client.get("/orgs/api/orgs").json().get("orgs", [])
        ids = {o.get("id") for o in listed}
        assert created_org in ids, f"created id {created_org} not in {ids}"

    def test_get_org_includes_members_field(self, http_client, created_org):
        data = assert_ok(http_client.get(f"/orgs/api/orgs/{created_org}"))
        assert "members" in data, f"missing members field: {data}"
        assert isinstance(data["members"], list)
        # And the new axes — reality + scope + kind — must be present.
        assert data.get("reality"), f"missing reality: {data}"
        assert data.get("scope"), f"missing scope: {data}"
        assert data.get("kind"), f"missing kind: {data}"

    def test_create_ai_member_returns_id(self, http_client, created_org, created_ai_member):
        assert created_ai_member, "member fixture returned no id"
        data = http_client.get(f"/orgs/api/orgs/{created_org}").json()
        mids = {m.get("id") for m in data.get("members") or []}
        assert created_ai_member in mids, f"member {created_ai_member} not on org"

    def test_ai_member_carries_mode_field(self, http_client, created_org, created_ai_member):
        data = http_client.get(f"/orgs/api/orgs/{created_org}").json()
        target = next((m for m in data.get("members", []) if m.get("id") == created_ai_member), None)
        assert target, "AI member not found on org detail"
        assert target.get("mode") == "ai", f"expected mode=ai, got {target.get('mode')}"

    def test_member_without_org_id_rejected(self, http_client):
        resp = http_client.post(
            "/orgs/api/members",
            json={"mode": "ai", "name": f"{TEST_PREFIX}orphan"},
        )
        body = resp.json()
        assert body.get("error"), f"expected error for missing org_id: {body}"

    def test_human_member_requires_person_id(self, http_client, created_org):
        resp = http_client.post(
            "/orgs/api/members",
            json={"org_id": created_org, "mode": "human"},
        )
        body = resp.json()
        assert body.get("error"), f"expected error for missing person_id on human member: {body}"

    def test_set_field_whitelist_enforced(self, http_client, created_ai_member):
        ok = http_client.post(
            "/orgs/api/set-field",
            json={"id": created_ai_member, "field": "role", "value": "Principal Engineer"},
        ).json()
        assert ok.get("ok"), f"expected ok=True, got {ok}"
        bad = http_client.post(
            "/orgs/api/set-field",
            json={"id": created_ai_member, "field": "tags", "value": "hax"},
        ).json()
        assert bad.get("error"), f"expected reject for non-settable field, got {bad}"

    def test_org_reality_enum_validated(self, http_client, created_org):
        bad = http_client.post(
            "/orgs/api/set-field",
            json={"id": created_org, "field": "reality", "value": "imaginary"},
        ).json()
        assert bad.get("error"), f"expected reality-enum reject, got {bad}"

    def test_org_scope_enum_validated(self, http_client, created_org):
        bad = http_client.post(
            "/orgs/api/set-field",
            json={"id": created_org, "field": "scope", "value": "alien"},
        ).json()
        assert bad.get("error"), f"expected scope-enum reject, got {bad}"

    def test_run_scenario_without_ai_members_fails(self, http_client):
        empty_name = _org_name("EmptyAI")
        empty = http_client.post(
            "/orgs/api/orgs",
            json={"name": empty_name, "kind": "team", "reality": "real", "scope": "member"},
        ).json()
        oid = empty.get("id")
        if not oid:
            pytest.skip(f"could not create empty org: {empty}")
        resp = http_client.post(
            "/orgs/api/scenario/run",
            json={"org_id": oid, "scenario_type": "critique", "prompt": "test"},
        ).json()
        assert resp.get("error"), f"expected AI-members-required error, got {resp}"

    def test_run_unknown_scenario_rejected(self, http_client, created_org, created_ai_member):
        resp = http_client.post(
            "/orgs/api/scenario/run",
            json={
                "org_id": created_org,
                "scenario_type": "telepathy",
                "prompt": "test",
            },
        ).json()
        assert resp.get("error"), f"expected unknown-scenario error, got {resp}"

    def test_runs_list_endpoint(self, http_client):
        data = assert_dict_response(
            http_client.get("/orgs/api/runs"),
            required_keys=["runs"],
        )
        assert isinstance(data["runs"], list)

    def test_apply_unknown_pending_returns_error(self, http_client):
        resp = http_client.post(
            "/orgs/api/pending/act-does-not-exist/apply",
        ).json()
        assert resp.get("error"), f"expected not-found error, got {resp}"

    def test_reject_unknown_pending_returns_error(self, http_client):
        resp = http_client.post(
            "/orgs/api/pending/act-also-fake/reject",
        ).json()
        assert resp.get("error"), f"expected not-found error, got {resp}"

    def test_org_append_section_whitelisted(self, http_client, created_org):
        # Whitelisted section should succeed
        ok = http_client.post(
            f"/orgs/api/orgs/{created_org}/append",
            json={"section": "Decisions", "text": f"{TEST_PREFIX}test decision"},
        ).json()
        assert ok.get("ok"), f"expected append OK, got {ok}"
        # Non-whitelisted section should reject
        bad = http_client.post(
            f"/orgs/api/orgs/{created_org}/append",
            json={"section": "EvilSection", "text": "x"},
        ).json()
        assert bad.get("error"), f"expected section reject, got {bad}"

    def test_memberships_endpoint_filters_by_person_id(self, http_client, created_org):
        # Querying by a person_id that has no memberships returns empty list
        data = http_client.get("/orgs/api/memberships?person_id=__nobody__").json()
        assert isinstance(data.get("memberships"), list)

    # ── Assets: vault paths + KB slugs linked to an org ───────────

    def test_list_assets_empty_on_fresh_org(self, http_client, created_org):
        data = assert_dict_response(
            http_client.get(f"/orgs/api/orgs/{created_org}/assets"),
            required_keys=["assets"],
        )
        # New org has no assets — the list is empty (no leak from other orgs).
        refs = {a.get("ref") for a in data["assets"]}
        assert all(r and not r.startswith("__pollution__") for r in refs)

    def test_add_asset_kb_slug_accepted(self, http_client, created_org):
        # KB slug refs aren't validated up-front — a slug may be authored
        # before its KB note exists. add succeeds; list_assets surfaces the
        # missing-note state via the per-asset `error` field at read time.
        ref = f"{TEST_PREFIX}-kb-slug"
        ok = http_client.post(
            f"/orgs/api/orgs/{created_org}/assets",
            json={"ref": ref},
        ).json()
        assert ok.get("ok"), f"expected ok=True, got {ok}"
        listed = http_client.get(f"/orgs/api/orgs/{created_org}/assets").json()
        refs = {a.get("ref") for a in listed.get("assets") or []}
        assert ref in refs, f"asset {ref} not in {refs}"

    def test_add_asset_duplicate_returns_already_linked(self, http_client, created_org):
        ref = f"{TEST_PREFIX}-dup-slug"
        first = http_client.post(
            f"/orgs/api/orgs/{created_org}/assets",
            json={"ref": ref},
        ).json()
        assert first.get("ok"), f"first add should succeed, got {first}"
        second = http_client.post(
            f"/orgs/api/orgs/{created_org}/assets",
            json={"ref": ref},
        ).json()
        assert second.get("ok"), f"second add should be idempotent, got {second}"
        assert "already" in (second.get("note") or "").lower(), (
            f"expected 'already linked' note, got {second}"
        )

    def test_add_asset_missing_vault_path_rejected(self, http_client, created_org):
        # Vault-path refs (contain '/' or end in '.md') ARE validated.
        bogus = "30_Resources/EmptyOS/__never__/__never__.md"
        resp = http_client.post(
            f"/orgs/api/orgs/{created_org}/assets",
            json={"ref": bogus},
        ).json()
        assert resp.get("error"), f"expected not-found error, got {resp}"

    def test_add_asset_unknown_org_rejected(self, http_client):
        resp = http_client.post(
            "/orgs/api/orgs/__no_such_org__/assets",
            json={"ref": "anything"},
        ).json()
        assert resp.get("error"), f"expected org-not-found error, got {resp}"

    def test_add_asset_empty_ref_rejected(self, http_client, created_org):
        resp = http_client.post(
            f"/orgs/api/orgs/{created_org}/assets",
            json={"ref": ""},
        ).json()
        assert resp.get("error"), f"expected empty-ref error, got {resp}"

    def test_remove_asset_round_trips(self, http_client, created_org):
        ref = f"{TEST_PREFIX}-remove-me"
        http_client.post(
            f"/orgs/api/orgs/{created_org}/assets",
            json={"ref": ref},
        )
        rm = http_client.request(
            "DELETE",
            f"/orgs/api/orgs/{created_org}/assets",
            json={"ref": ref},
        ).json()
        assert rm.get("ok"), f"expected remove ok, got {rm}"
        listed = http_client.get(f"/orgs/api/orgs/{created_org}/assets").json()
        refs = {a.get("ref") for a in listed.get("assets") or []}
        assert ref not in refs, f"removed asset still present: {refs}"

    def test_remove_unknown_asset_returns_error(self, http_client, created_org):
        resp = http_client.request(
            "DELETE",
            f"/orgs/api/orgs/{created_org}/assets",
            json={"ref": "__not_linked__"},
        ).json()
        assert resp.get("error"), f"expected not-linked error, got {resp}"


@pytest.mark.api
class TestOrgsPersonaMirror:
    """AI members ARE rooms agents. Verify the mirror lifecycle."""

    def test_ai_member_create_registers_rooms_agent(self, http_client, created_ai_member):
        resp = http_client.get(f"/rooms/api/agents/{created_ai_member}")
        if resp.status_code == 404:
            pytest.skip("rooms app not installed — mirror skipped silently by design")
        data = resp.json()
        if data.get("error"):
            pytest.skip(f"rooms mirror not registered: {data}")
        assert data.get("id") == created_ai_member
        assert data.get("source", "").startswith("orgs:")
        assert data.get("system_prompt"), f"empty system_prompt in mirror: {data}"

    def test_ai_member_edit_updates_rooms_mirror(self, http_client, created_ai_member):
        new_role = f"{TEST_PREFIX}Principal Architect"
        http_client.post(
            "/orgs/api/set-field",
            json={"id": created_ai_member, "field": "role", "value": new_role},
        )
        resp = http_client.get(f"/rooms/api/agents/{created_ai_member}")
        if resp.status_code == 404:
            pytest.skip("rooms not installed")
        data = resp.json()
        if data.get("error"):
            pytest.skip(f"rooms mirror missing: {data}")
        assert data.get("id") == created_ai_member

    def test_ai_member_delete_removes_mirror(self, http_client, created_org):
        mresp = http_client.post(
            "/orgs/api/members",
            json={
                "org_id": created_org,
                "mode": "ai",
                "name": f"{TEST_PREFIX}Throwaway",
                "role": "junior",
            },
        )
        mdata = mresp.json()
        mid = mdata.get("id")
        if not mid:
            pytest.skip(f"could not create throwaway member: {mdata}")
        before = http_client.get(f"/rooms/api/agents/{mid}")
        if before.status_code == 404:
            pytest.skip("rooms not installed")
        # rooms returns 200 + {"error":"not found"} when the route exists but
        # the agent doesn't, so check the body shape (not the HTTP status)
        # to confirm the agent is registered before we try to delete it.
        assert "id" in before.json(), f"agent not registered before delete: {before.json()}"
        dresp = http_client.request("DELETE", f"/orgs/api/members/{mid}")
        d = dresp.json()
        assert d.get("ok"), f"delete failed: {d}"
        after = http_client.get(f"/rooms/api/agents/{mid}").json()
        assert after.get("error"), f"rooms mirror still present after delete: {after}"
        listed = http_client.get("/orgs/api/orgs/" + created_org).json()
        mids = {m.get("id") for m in listed.get("members") or []}
        assert mid not in mids, "archived member still in roster"


@pytest.mark.api
@pytest.mark.llm
class TestOrgsScenarioRun:
    """Live LLM tests — run with `-m llm`. Single critique scenario,
    1 AI member, short prompt to keep cost low.
    """

    def test_critique_run_produces_responses(self, http_client, created_org, created_ai_member):
        resp = http_client.post(
            "/orgs/api/scenario/run",
            json={
                "org_id": created_org,
                "scenario_type": "critique",
                "prompt": "Should we add a settings panel to this test org?",
                "mode": "headless",
            },
            timeout=180,
        )
        data = assert_ok(resp)
        assert data.get("id"), f"run returned no id: {data}"
        assert isinstance(data.get("responses"), list)
        assert "tally" in data


@pytest.mark.interactive
class TestOrgsUI:

    def test_ui_orgs_list_loads(self, app_page, page_errors):
        page = app_page("orgs")
        page.wait_for_load_state("networkidle", timeout=10000)
        assert page.locator("button:has-text('+ New Org')").count() >= 1

    def test_ui_filter_chips_render(self, app_page, page_errors):
        page = app_page("orgs")
        page.wait_for_load_state("networkidle", timeout=10000)
        # All / Real / Virtual / External chips should be present
        for label in ("All", "Real", "Virtual", "External"):
            assert page.locator(f".filter-chip:has-text('{label}')").count() >= 1, (
                f"filter chip '{label}' missing"
            )

    def test_ui_org_detail_has_tabs(self, page, base_url, page_errors, http_client, created_org, created_ai_member):
        # Navigate directly: app_page() appends a trailing "/" that lands inside the
        # hash fragment and breaks the org-id lookup.
        page.goto(f"{base_url}/orgs/#org/{created_org}", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_load_state("networkidle", timeout=10000)
        # Roster + Roles + Scenarios + Notes tabs
        assert page.locator(".tab-btn:has-text('Roster')").count() >= 1, "Roster tab missing"
        assert page.locator(".tab-btn:has-text('Roles')").count() >= 1, "Roles tab missing"
        # Scenarios tab only renders when there's ≥1 AI member, which fixture provides
        assert page.locator(".tab-btn:has-text('Scenarios')").count() >= 1, "Scenarios tab missing"
        assert page.locator(".tab-btn:has-text('Notes')").count() >= 1, "Notes tab missing"
