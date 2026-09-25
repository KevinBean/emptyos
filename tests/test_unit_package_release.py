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
import sys
from pathlib import Path

import pytest

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
