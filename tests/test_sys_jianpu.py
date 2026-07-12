"""System tests: Jianpu Composer — numbered-notation songs as vault notes.

API CRUD against the live daemon + the notation grammar pinned via
page.evaluate against the JIANPU global (the renderer is the single
implementation of the grammar, so the contract tests live where it runs).
Tests skip cleanly when the app isn't installed (new apps need a Store
install + restart).
"""

from __future__ import annotations

import pytest

from helpers import assert_ok

PREFIX = "/jianpu"


def _installed(http_client) -> bool:
    return http_client.get(PREFIX + "/api/songs").status_code == 200


def _create(http_client, title, **kw):
    body = assert_ok(http_client.post(PREFIX + "/api/songs", json={"title": title, **kw}))
    assert body.get("ok") is True, f"create failed: {body}"
    return body["file"]


@pytest.mark.api
class TestJianpuAPI:
    def test_page_loads(self, http_client):
        if not _installed(http_client):
            pytest.skip("jianpu not installed on this daemon")
        r = http_client.get(PREFIX + "/")
        assert r.status_code == 200
        assert "Jianpu" in r.text

    def test_create_and_list(self, http_client):
        if not _installed(http_client):
            pytest.skip("jianpu not installed")
        f = _create(http_client, "PLAYWRIGHT-TEST- song create")
        listed = assert_ok(http_client.get(PREFIX + "/api/songs"))
        assert f in [s["file"] for s in listed.get("songs", [])]
        http_client.delete(f"{PREFIX}/api/songs/{f}")

    def test_create_defaults(self, http_client):
        if not _installed(http_client):
            pytest.skip("jianpu not installed")
        f = _create(http_client, "PLAYWRIGHT-TEST- song defaults")
        detail = assert_ok(http_client.get(f"{PREFIX}/api/songs/{f}"))
        assert detail.get("key") == "1=C"
        assert detail.get("meter") == "4/4"
        assert detail.get("tempo") == 80
        assert detail.get("source") == ""
        http_client.delete(f"{PREFIX}/api/songs/{f}")

    def test_save_source_roundtrip(self, http_client):
        if not _installed(http_client):
            pytest.skip("jianpu not installed")
        f = _create(http_client, "PLAYWRIGHT-TEST- song save")
        src = "3 3 5 6_1'_ | 1' 6 5 5 - |\nL: 好一朵美丽的茉莉花"
        body = assert_ok(http_client.post(f"{PREFIX}/api/songs/{f}", json={"source": src, "tempo": 96}))
        assert body.get("ok") is True
        detail = assert_ok(http_client.get(f"{PREFIX}/api/songs/{f}"))
        assert detail.get("source") == src
        assert detail.get("tempo") == 96
        http_client.delete(f"{PREFIX}/api/songs/{f}")

    def test_save_preserves_fence(self, http_client):
        """The note body must keep the ```jianpu fence across saves (the note
        stays hand-readable; the fence is the extraction anchor)."""
        if not _installed(http_client):
            pytest.skip("jianpu not installed")
        f = _create(http_client, "PLAYWRIGHT-TEST- song fence")
        assert_ok(http_client.post(f"{PREFIX}/api/songs/{f}", json={"source": "1 2 3 |"}))
        assert_ok(http_client.post(f"{PREFIX}/api/songs/{f}", json={"source": "5 6 7 |"}))
        detail = assert_ok(http_client.get(f"{PREFIX}/api/songs/{f}"))
        assert detail.get("source") == "5 6 7 |", "second save must replace, not append"
        http_client.delete(f"{PREFIX}/api/songs/{f}")

    def test_create_requires_title(self, http_client):
        if not _installed(http_client):
            pytest.skip("jianpu not installed")
        body = assert_ok(http_client.post(PREFIX + "/api/songs", json={}))
        assert "error" in body

    def test_detail_missing_song(self, http_client):
        if not _installed(http_client):
            pytest.skip("jianpu not installed")
        body = assert_ok(http_client.get(PREFIX + "/api/songs/PLAYWRIGHT-TEST-nope.md"))
        assert "error" in body

    def test_delete_removes_from_list(self, http_client):
        if not _installed(http_client):
            pytest.skip("jianpu not installed")
        f = _create(http_client, "PLAYWRIGHT-TEST- song delete")
        body = assert_ok(http_client.delete(f"{PREFIX}/api/songs/{f}"))
        assert body.get("ok") is True
        listed = assert_ok(http_client.get(PREFIX + "/api/songs"))
        assert f not in [s["file"] for s in listed.get("songs", [])]


@pytest.mark.interactive
class TestJianpuGrammar:
    """The notation grammar contract, pinned via the JIANPU global on the
    composer page. These run wherever the renderer runs — no fixtures."""

    @pytest.fixture(autouse=True)
    def _open(self, page, base_url, http_client):
        if not _installed(http_client):
            pytest.skip("jianpu not installed")
        page.goto(f"{base_url}{PREFIX}/")
        page.wait_for_function("() => !!window.JIANPU")

    def test_dotted_octave_extend(self, page):
        r = page.evaluate(
            "() => { const p = JIANPU.parse(\"5. 6 | 1' - - |\", {meter:'4/4'});"
            " const n = p.systems[0].tokens.filter(t => t.kind === 'note');"
            " return {count: n.length, dotted: n[0].dotted, oct: n[2].octave, ext: n[2].extendBeats}; }"
        )
        assert r == {"count": 3, "dotted": True, "oct": 1, "ext": 2}

    def test_underline_durations_and_beams(self, page):
        r = page.evaluate(
            "() => { const p = JIANPU.parse(\"6_1'_ 5__ | 3 |\", {meter:'4/4'});"
            " const n = p.systems[0].tokens.filter(t => t.kind === 'note');"
            " return {ul: n.map(x => x.underlines), beam01: n[0].beamGroup === n[1].beamGroup"
            "         && n[0].beamGroup != null, solo: n[2].beamGroup}; }"
        )
        assert r["ul"] == [1, 1, 2, 0]
        assert r["beam01"] is True
        assert r["solo"] is None

    def test_cjk_lyrics_map_to_pitched_notes_only(self, page):
        r = page.evaluate(
            "() => { const p = JIANPU.parse('3 0 5 | 6 - |\\nL: 好一朵', {meter:'4/4'});"
            " const n = p.systems[0].tokens.filter(t => t.kind === 'note');"
            " return n.map(x => x.lyric); }"
        )
        # rest (0) consumes no syllable; dash consumes no syllable
        assert r == ["好", None, "一", "朵"]

    def test_lyrics_melisma_skip(self, page):
        r = page.evaluate(
            "() => { const p = JIANPU.parse('1 2 3 |\\nL: 好-丽', {meter:'3/4'});"
            " const n = p.systems[0].tokens.filter(t => t.kind === 'note');"
            " return n.map(x => x.lyric); }"
        )
        assert r == ["好", None, "丽"]

    def test_bad_bar_is_soft_warning_not_failure(self, page):
        r = page.evaluate(
            "() => { const p = JIANPU.parse('1 2 |', {meter:'4/4'});"
            " return {systems: p.systems.length, warnings: p.warnings.length}; }"
        )
        assert r["systems"] == 1
        assert r["warnings"] >= 1

    def test_render_smoke_svg_digits(self, page):
        count = page.evaluate(
            "() => { const el = document.createElement('div'); document.body.appendChild(el);"
            " const p = JIANPU.parse(\"1 2 3 | 5' - |\", {meter:'4/4'});"
            " JIANPU.render(el, p, {fontSize: 24});"
            " const out = {svgs: el.querySelectorAll('svg').length,"
            "   digits: Array.from(el.querySelectorAll('text')).filter(t => /^[0-7]$/.test(t.textContent)).length};"
            " el.remove(); return out; }"
        )
        assert count["svgs"] == 1
        assert count["digits"] == 4
