"""Unit tests for the release collector's do-not-ship filter.

`package-release.py` copies from the **working tree**, not from a git snapshot,
so anything gitignored inside an included directory would ship unless filtered.
It did: a `standard` artifact carried 147 such files — `tests/personal/`,
`tests/_dogfood-phone/*.png` (screenshots of a real vault), `emptyos/.obsidian/`,
and a live `skills/tool-google-maps/.env` API key. `release-public.py` was never
affected (it snapshots via `git archive HEAD`), which is why this hid for so long
— only the *distributable artifacts* leaked.

These tests pin both directions: the filter catches what git hides, and stays
silent on what is meant to ship.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tomllib
from pathlib import Path

import pytest

from helpers import public_snapshot

ROOT = Path(__file__).resolve().parents[1]


def _load():
    """package-release.py has a hyphen, so it can't be imported by name."""
    spec = importlib.util.spec_from_file_location(
        "package_release", ROOT / "scripts" / "package-release.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["package_release"] = mod
    spec.loader.exec_module(mod)
    return mod


pr = _load()


class TestIsSecretName:
    @pytest.mark.parametrize("name", [
        ".env", ".env.local", "server.pem", "private.key",
        "credentials.json", "gmail-token.json", "youtube-client.json",
        "id_rsa", "cert.pfx",
    ])
    def test_flags_credentials(self, name):
        assert pr.is_secret_name(Path("some/dir") / name) is True

    @pytest.mark.parametrize("name", [
        ".env.example", ".env.sample", "config.template",
        "app.py", "manifest.toml", "README.md", "icon.png",
    ])
    def test_passes_templates_and_normal_files(self, name):
        # A template documents the shape of a credential; it isn't one.
        assert pr.is_secret_name(Path("some/dir") / name) is False


class TestGitignored:
    def test_empty_input_never_shells_out(self):
        assert pr.gitignored([]) == set()

    def test_flags_a_gitignored_path_and_not_a_tracked_one(self):
        tracked = ROOT / "release.toml"
        # data/ is gitignored; use a path git will classify regardless of whether
        # it exists on this machine.
        ignored = ROOT / "data" / "syslog.db"

        result = pr.gitignored([tracked, ignored])

        assert ignored.resolve() in result
        assert tracked.resolve() not in result

    def test_survives_windows_newline_translation(self):
        """The first version passed paths with text=True, so Windows rewrote "\\n"
        to "\\r\\n" and git saw filenames ending in a carriage return — it then
        C-quoted them back and nothing matched. Passing many paths at once is what
        exposed that, so keep doing it here."""
        paths = [ROOT / "release.toml", ROOT / "pyproject.toml",
                 ROOT / "data" / "a.db", ROOT / "data" / "b.db"]

        result = pr.gitignored(paths)

        assert all("\r" not in str(p) for p in result)
        assert (ROOT / "data" / "a.db").resolve() in result
        assert (ROOT / "data" / "b.db").resolve() in result
        assert (ROOT / "release.toml").resolve() not in result


class TestVersionIsOneNumber:
    """release.toml is the single source of truth for the version.

    It was fragmented across three places: release.toml (authoritative —
    package-release.py stamps it into every MANIFEST.json), pyproject.toml (0.1.0,
    stale), and the macOS build script (0.1.0, hardcoded, bumped by nobody). The
    product's About panel shows the user a version number, so "which one is real"
    stopped being an academic question.

    Asserted here since that collapse — but nothing ran it on the release path,
    so the two drifted apart again across seven consecutive releases
    (v0.5.7 -> v0.6.4) with this test red the whole time. The assertion now lives
    in `scripts/check_version_sync.py`, registered in preflight's `always` +
    `release` scopes so it fires where the bump happens; this delegates to it
    rather than keeping a second copy that could disagree.
    """

    def _check(self):
        import importlib.util

        path = ROOT / "scripts" / "check_version_sync.py"
        spec = importlib.util.spec_from_file_location("check_version_sync", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_pyproject_matches_release_toml(self):
        hits = self._check().findings()
        assert hits == [], (
            "pyproject.toml version drifted from release.toml — bump both: "
            + "; ".join(h["detail"] for h in hits)
        )

    def test_checker_detects_drift(self):
        """Both directions — the pin must actually fail on a drifted tree."""
        import tempfile

        cvs = self._check()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "release.toml").write_text(
                '[release]\nversion = "9.9.9"\n', encoding="utf-8"
            )
            (root / "pyproject.toml").write_text(
                '[project]\nname = "x"\nversion = "1.1.1"\n', encoding="utf-8"
            )
            hits = cvs.findings(root)
            assert len(hits) == 1
            assert hits[0]["issue"] == "drift"
            assert hits[0]["found"] == "1.1.1"
            assert hits[0]["expected"] == "9.9.9"

            (root / "pyproject.toml").write_text(
                '[project]\nname = "x"\nversion = "9.9.9"\n', encoding="utf-8"
            )
            assert cvs.findings(root) == []

    def test_macos_build_reads_the_version_rather_than_hardcoding_it(self):
        src = (ROOT / "products" / "desktop-macos" / "build_app.py").read_text(encoding="utf-8")
        assert 'VERSION = "0.1.0"' not in src
        assert "release.toml" in src


class TestCollectorDropsSecrets:
    def test_the_known_leak_is_gone(self):
        """The exact file that shipped a live API key."""
        env = ROOT / "skills" / "tool-google-maps" / ".env"
        if not env.exists():
            pytest.skip("skills/tool-google-maps/.env not present on this machine")

        assert pr.is_secret_name(env) is True
        assert env.resolve() in pr.gitignored([env])


@pytest.mark.skipif(
    "dev" not in tomllib.loads((ROOT / "release.toml").read_text(encoding="utf-8")).get("tiers", {})
    and public_snapshot(),
    reason="no `dev` tier in release.toml (the public snapshot drops it)",
)
class TestDeveloperServicePackaging:
    def test_dev_tier_declares_both_external_lab_services(self):
        tier = pr.resolve_tier(pr.load_release(), "dev")
        assert tier["services"] == ["chatbot", "external_lab"]

    def test_dev_artifact_collects_service_code_not_runtime_state(self):
        release = pr.load_release()
        # Exercise the real service collector without walking every app inherited
        # by the full developer tier (that belongs to the package --check gate).
        files = pr.collect_files(release, {
            "apps": [], "plugins": [], "skills": [],
            "services": ["chatbot", "external_lab"],
        })
        rels = {rel.as_posix() for _, rel in files}

        assert "services/chatbot/main.py" in rels
        assert "services/external_lab/main.py" in rels
        assert not any(rel.endswith("/.env") for rel in rels)
        assert "services/chatbot/sites.toml" not in rels
        assert not any(rel.startswith("services/chatbot/data/") for rel in rels)


class TestAgentSkillPackaging:
    def test_collects_canonical_agent_skill_without_duplicate_legacy_copy(
        self, tmp_path, monkeypatch
    ):
        canonical = (
            tmp_path / ".agents" / "skills" / "eos-ai-conversation-ingest"
        )
        canonical.mkdir(parents=True)
        (canonical / "SKILL.md").write_text("# Canonical\n", encoding="utf-8")

        monkeypatch.setattr(pr, "ROOT", tmp_path)
        monkeypatch.setattr(pr, "gitignored", lambda paths: set())

        files = pr.collect_files(
            {"exclude": {"patterns": []}, "include": {"paths": []}},
            {
                "apps": [],
                "plugins": [],
                "skills": ["eos-ai-conversation-ingest"],
                "services": [],
            },
        )
        rels = {rel.as_posix() for _, rel in files}

        assert rels == {
            ".agents/skills/eos-ai-conversation-ingest/SKILL.md"
        }
        assert not any(rel.startswith("skills/") for rel in rels)


# ── the dictionary definition pack ────────────────────────────────────────
#
# A generated pack is gitignored, so collect_files drops it by design. A tier
# that declares `definition_pack` gets it copied in explicitly, after a check
# with the dictionary's own reader — and only when asked, because an image
# without it turns every dictionary lookup into a paid model call.

# The public snapshot has neither the dictionary app nor the englishos-cloud
# tier, so the tests that need them skip there. Keyed on public_snapshot()
# as well, so a private move still fails loudly.
needs_dictionary = pytest.mark.skipif(
    not pr.DEFINITION_PACK_MODULE.exists() and public_snapshot(),
    reason="dictionary app absent (public snapshot)",
)
needs_cloud_tier = pytest.mark.skipif(
    "englishos-cloud" not in tomllib.loads((ROOT / "release.toml").read_text(encoding="utf-8"))["tiers"]  # release-filter: optional
    and public_snapshot(),
    reason="englishos-cloud tier absent (public snapshot)",
)


def _dp():
    spec = importlib.util.spec_from_file_location("dp_for_release_tests", pr.DEFINITION_PACK_MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pack(path, n=2):
    entries = {w: {"word": w, "part_of_speech": "noun", "definition": f"meaning of {w}"}
               for w in ["luck", "run", "walk"][:n]}
    _dp().write_pack(path, entries, {"provider": "p", "model": "m"})
    return path


DECLARING = {"cloud": {"definition_pack": "x/p.sqlite"}, "plain": {}}


class TestDefinitionPackChoice:
    def test_a_tier_that_declares_none_needs_no_flag(self):
        assert pr.definition_pack_choice(DECLARING, "plain", []) == (None, None)

    def test_the_flag_names_the_source(self):
        dest, src = pr.definition_pack_choice(DECLARING, "cloud", ["--definition-pack=build/p.sqlite"])
        assert dest == "x/p.sqlite" and src == Path("build/p.sqlite")

    def test_an_explicit_opt_out_ships_without_it(self):
        assert pr.definition_pack_choice(DECLARING, "cloud", ["--no-definition-pack"]) == (None, None)

    def test_silence_is_refused(self):
        with pytest.raises(SystemExit) as exc:
            pr.definition_pack_choice(DECLARING, "cloud", ["--check"])
        assert "--definition-pack" in str(exc.value)

    def test_the_flag_without_equals_is_refused(self):
        # "--definition-pack build/p.sqlite": the path lands among the tier args.
        with pytest.raises(SystemExit):
            pr.definition_pack_choice(DECLARING, "cloud", ["--definition-pack"])

    def test_a_tier_that_extends_a_declaring_tier_inherits_the_requirement(self):
        tiers = {**DECLARING, "cloud-plus": {"extends": "cloud"}}
        with pytest.raises(SystemExit):
            pr.definition_pack_choice(tiers, "cloud-plus", [])
        assert pr.definition_pack_choice(tiers, "cloud-plus", ["--definition-pack=p"])[0] == "x/p.sqlite"


@needs_dictionary
class TestCheckDefinitionPack:
    def test_a_good_pack_reports_its_provenance(self, tmp_path):
        import hashlib

        path = _pack(tmp_path / "p.sqlite")
        info = pr.check_definition_pack(path)
        assert info["entries"] == 2 and info["model"] == "m"
        assert info["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()

    def test_the_checked_pack_is_left_closed(self, tmp_path):
        # An open handle would lock the file on Windows for the rest of the run.
        path = _pack(tmp_path / "p.sqlite")
        pr.check_definition_pack(path)
        path.unlink()
        assert not path.exists()

    def test_a_missing_file_is_refused(self, tmp_path):
        with pytest.raises(SystemExit) as exc:
            pr.check_definition_pack(tmp_path / "nope.sqlite")
        assert "not found" in str(exc.value)

    def test_a_file_the_dictionary_cannot_open_is_refused(self, tmp_path):
        bad = tmp_path / "p.sqlite"
        bad.write_bytes(b"not a database")
        with pytest.raises(SystemExit):
            pr.check_definition_pack(bad)

    def test_a_pack_the_dictionary_would_refuse_is_refused_even_with_entries(self, tmp_path):
        # A future schema: rows are there, but the reader would not use them.
        import sqlite3

        path = _pack(tmp_path / "p.sqlite")
        con = sqlite3.connect(path)
        con.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
        con.commit()
        con.close()
        with pytest.raises(SystemExit) as exc:
            pr.check_definition_pack(path)
        assert "unusable" in str(exc.value)

    def test_an_empty_pack_is_refused(self, tmp_path):
        with pytest.raises(SystemExit) as exc:
            pr.check_definition_pack(_pack(tmp_path / "p.sqlite", n=0))
        assert "empty" in str(exc.value)


@needs_dictionary
def test_package_copies_the_pack_after_the_audit_and_records_it(tmp_path, monkeypatch):
    src = _pack(tmp_path / "build" / "p.sqlite")
    release = {"release": {"version": "9.9.9"},
               "tiers": {"cloud": {"apps": [], "plugins": [],
                                   "definition_pack": "englishos-cloud/definitions.sqlite"}}}  # release-filter: optional
    audited = []
    monkeypatch.setattr(pr, "load_release", lambda: release)
    monkeypatch.setattr(pr, "resolve_tier", lambda rel, name: {
        "apps": [], "plugins": [], "skills": [], "services": []})
    monkeypatch.setattr(pr, "run_safety_checks", lambda: True)
    monkeypatch.setattr(pr, "collect_files", lambda rel, tier: [])
    monkeypatch.setattr(pr, "DIST", tmp_path / "dist")
    # The audit checks what the collector gathered; the pack is added after it.
    monkeypatch.setattr(pr, "audit_output", lambda out: audited.append(sorted(
        p.name for p in out.rglob("*") if p.is_file())))
    pr.package("cloud", flags=[f"--definition-pack={src}"])
    out = tmp_path / "dist" / "emptyos-cloud-9.9.9"
    assert audited == [[]]
    shipped = out / "englishos-cloud" / "definitions.sqlite"  # release-filter: optional
    assert shipped.read_bytes() == src.read_bytes()
    manifest = json.loads((out / "MANIFEST.json").read_text(encoding="utf-8"))
    import hashlib

    assert manifest["definition_pack"]["entries"] == 2
    assert manifest["definition_pack"]["path"] == "englishos-cloud/definitions.sqlite"  # release-filter: optional
    assert manifest["definition_pack"]["sha256"] == hashlib.sha256(shipped.read_bytes()).hexdigest()
    assert manifest["file_count"] == 1


@needs_dictionary
def test_a_pack_that_changes_during_packaging_is_not_shipped(tmp_path, monkeypatch):
    src = _pack(tmp_path / "build" / "p.sqlite")
    release = {"release": {"version": "9.9.9"},
               "tiers": {"cloud": {"apps": [], "plugins": [],
                                   "definition_pack": "englishos-cloud/definitions.sqlite"}}}  # release-filter: optional
    monkeypatch.setattr(pr, "load_release", lambda: release)
    monkeypatch.setattr(pr, "resolve_tier", lambda rel, name: {
        "apps": [], "plugins": [], "skills": [], "services": []})
    monkeypatch.setattr(pr, "run_safety_checks", lambda: True)
    monkeypatch.setattr(pr, "collect_files", lambda rel, tier: [])
    monkeypatch.setattr(pr, "DIST", tmp_path / "dist")
    monkeypatch.setattr(pr, "audit_output", lambda out: None)
    # Stands in for a rebuild landing between the check and the copy.
    monkeypatch.setattr(pr.shutil, "copy2", lambda a, b: Path(b).write_bytes(b"rebuilt"))
    with pytest.raises(SystemExit) as exc:
        pr.package("cloud", flags=[f"--definition-pack={src}"])
    assert "changed" in str(exc.value)
    assert not (tmp_path / "dist" / "emptyos-cloud-9.9.9").exists()


@needs_cloud_tier
def test_the_pack_destination_is_gitignored_in_the_repo():
    # A copy left there for local testing must not ride along in other tiers,
    # which all collect englishos-cloud/.
    tier = tomllib.loads((ROOT / "release.toml").read_text(encoding="utf-8"))["tiers"]["englishos-cloud"]  # release-filter: optional
    dest = (ROOT / tier["definition_pack"]).resolve()
    assert dest in pr.gitignored([dest])


@needs_cloud_tier
def test_the_hosted_config_reads_the_pack_where_packaging_puts_it():
    tier = tomllib.loads((ROOT / "release.toml").read_text(encoding="utf-8"))["tiers"]["englishos-cloud"]  # release-filter: optional
    hosted = tomllib.loads((ROOT / "englishos-cloud" / "emptyos.toml.example").read_text(encoding="utf-8"))  # release-filter: optional
    dest = tier["definition_pack"]
    # The config is /app/emptyos.toml, so its relative path resolves under /app.
    assert hosted["apps"]["dictionary"]["definition_pack_path"] == dest
    # ...and the image must actually put the artifact's copy of that directory
    # under /app, not under a volume that would hide it.
    top = dest.split("/", 1)[0]
    lines = (ROOT / "englishos-cloud" / "Dockerfile").read_text(encoding="utf-8").splitlines()  # release-filter: optional
    copies = [ln.split()[1:] for ln in lines if ln.startswith("COPY ")]
    assert [f"{top}/", f"./{top}/"] in copies
    volumes = " ".join(ln for ln in lines if ln.startswith("VOLUME"))
    assert f"/app/{top}" not in volumes
