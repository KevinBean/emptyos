"""Unit tests for the product self-updater (products/_shared/updater.py).

An updater is the one component that downloads a file from the internet and then
executes it. Its safety properties are the whole point, so they are pinned here:

- a checksum mismatch never installs (a truncated download must not become an app)
- a version without its `.ok` marker is invisible (a crash mid-install is not a
  half-installed app; it is no app)
- the running version is never pruned
- an archive cannot write outside its own directory
- 0.5.10 is newer than 0.5.9 (a string compare says otherwise, and would strand
  every user on .9 forever)

No network: `download` is exercised against a `file://` URL, which is a real
urlopen through the real code path.
"""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "products"))

from _shared import updater as up  # noqa: E402


def make_zip(path: Path, version: str, *, exe: bool = True, escape: bool = False) -> Path:
    """A release archive shaped like the real one: everything under app-<version>/."""
    prefix = f"app-{version}/"
    with zipfile.ZipFile(path, "w") as zf:
        if exe:
            zf.writestr(prefix + "EmptyOS.exe", b"MZ fake executable")
        zf.writestr(prefix + "_internal/MANIFEST.json", json.dumps({"version": version}))
        if escape:
            zf.writestr(prefix + "../../evil.txt", b"pwned")
    return path


def release_for(zip_path: Path, version: str) -> up.Release:
    return up.Release(
        version=version,
        url=zip_path.resolve().as_uri(),
        sha256=hashlib.sha256(zip_path.read_bytes()).hexdigest(),
        size=zip_path.stat().st_size,
    )


# ── versions ─────────────────────────────────────────────────────────────────
class TestVersionOrdering:
    @pytest.mark.parametrize("candidate,current,expected", [
        ("0.5.7", "0.5.6", True),
        ("0.5.6", "0.5.6", False),
        ("0.5.5", "0.5.6", False),
        ("0.6.0", "0.5.99", True),
        ("1.0.0", "0.9.9", True),
    ])
    def test_is_newer(self, candidate, current, expected):
        assert up.is_newer(candidate, current) is expected

    def test_double_digit_patch_beats_single(self):
        """The bug a string compare would ship: "0.5.10" < "0.5.9" as text, so
        every user would sit on .9 and never be offered .10 again."""
        assert up.is_newer("0.5.10", "0.5.9") is True
        assert up.is_newer("0.5.9", "0.5.10") is False

    def test_junk_version_does_not_raise(self):
        # The stub parses versions too, and it must always start.
        assert up.parse_version("not-a-version") == (0,)
        assert up.is_newer("", "0.5.6") is False


# ── the feed ─────────────────────────────────────────────────────────────────
class TestParseFeed:
    def _feed(self, **over) -> dict:
        entry = {"url": "https://example.test/a.zip", "sha256": "a" * 64, "size": 10}
        entry.update(over.pop("entry", {}))
        data = {"version": "0.5.7", "platforms": {"windows-x64": entry}}
        data.update(over)
        return data

    def test_reads_a_good_feed(self):
        r = up.parse_feed(self._feed(), platform="windows-x64")
        assert (r.version, r.size) == ("0.5.7", 10)
        assert r.sha256 == "a" * 64

    def test_refuses_a_feed_with_no_checksum(self):
        """Without a checksum we cannot tell a truncated download from a good one,
        and we would be executing whatever arrived."""
        with pytest.raises(up.UpdateError, match="sha256"):
            up.parse_feed(self._feed(entry={"sha256": ""}), platform="windows-x64")

    def test_refuses_a_truncated_checksum(self):
        with pytest.raises(up.UpdateError, match="sha256"):
            up.parse_feed(self._feed(entry={"sha256": "abc"}), platform="windows-x64")

    def test_refuses_a_feed_with_no_build_for_this_platform(self):
        with pytest.raises(up.UpdateError, match="macos"):
            up.parse_feed(self._feed(), platform="macos-universal")

    def test_refuses_a_versionless_feed(self):
        with pytest.raises(up.UpdateError, match="version"):
            up.parse_feed({"platforms": {}}, platform="windows-x64")


class TestCheck:
    def test_offline_is_not_an_error(self, monkeypatch):
        """A laptop is offline half the time. That must be a no-op, not a dialog."""
        def boom(*a, **k):
            raise OSError("no network")
        monkeypatch.setattr(up, "fetch_feed", boom)

        assert up.check("https://example.test/latest.json", "0.5.6") is None

    def test_no_feed_configured_is_a_no_op(self):
        assert up.check("", "0.5.6") is None


# ── download + install ───────────────────────────────────────────────────────
class TestDownload:
    def test_verifies_and_keeps(self, tmp_path):
        src = make_zip(tmp_path / "src.zip", "0.5.7")
        root = tmp_path / "install"
        root.mkdir()

        got = up.download(release_for(src, "0.5.7"), root)

        assert got.exists()
        assert got.name == "app-0.5.7.zip"
        assert not list((root / "updates").glob("*.partial"))  # nothing left over

    def test_a_corrupt_download_installs_nothing(self, tmp_path):
        src = make_zip(tmp_path / "src.zip", "0.5.7")
        root = tmp_path / "install"
        root.mkdir()
        lying = up.Release(version="0.5.7", url=src.resolve().as_uri(),
                           sha256="b" * 64, size=src.stat().st_size)

        with pytest.raises(up.UpdateError, match="checksum mismatch"):
            up.download(lying, root)

        # The bad bytes are gone — they can't be picked up by a later run.
        assert not list((root / "updates").glob("*.partial"))
        assert not list((root / "updates").glob("*.zip"))

    def test_reuses_an_already_verified_download(self, tmp_path):
        src = make_zip(tmp_path / "src.zip", "0.5.7")
        root = tmp_path / "install"
        root.mkdir()
        release = release_for(src, "0.5.7")
        first = up.download(release, root)
        src.unlink()   # the server is now unreachable

        again = up.download(release, root)   # must not re-fetch

        assert again == first


class TestInstall:
    def test_installs_and_marks_it_good(self, tmp_path):
        root = tmp_path / "install"
        root.mkdir()
        zip_path = make_zip(tmp_path / "app-0.5.7.zip", "0.5.7")

        app_dir = up.install(zip_path, root, "0.5.7")

        assert app_dir == root / "app-0.5.7"
        assert (app_dir / "EmptyOS.exe").exists()
        assert (app_dir / "_internal" / "MANIFEST.json").exists()
        assert (app_dir / up.OK_MARKER).read_text() == "0.5.7"

    def test_a_version_without_its_ok_marker_is_invisible(self, tmp_path):
        """The safety property: a crash mid-install leaves a directory, and the
        stub must not launch it."""
        root = tmp_path / "install"
        (root / "app-0.5.7").mkdir(parents=True)
        (root / "app-0.5.7" / "EmptyOS.exe").write_bytes(b"half a download")

        assert up.installed_versions(root) == []
        assert up.newest_version(root) is None

    def test_refuses_an_archive_with_no_executable(self, tmp_path):
        root = tmp_path / "install"
        root.mkdir()
        zip_path = make_zip(tmp_path / "app-0.5.7.zip", "0.5.7", exe=False)

        with pytest.raises(up.UpdateError, match="no executable"):
            up.install(zip_path, root, "0.5.7")
        assert not (root / "app-0.5.7").exists()   # nothing left behind

    def test_refuses_to_write_outside_its_own_directory(self, tmp_path):
        """Zip-slip. The checksum only proves the archive is the one we were told
        to expect — not that we were told the truth."""
        root = tmp_path / "install"
        root.mkdir()
        zip_path = make_zip(tmp_path / "app-0.5.7.zip", "0.5.7", escape=True)

        with pytest.raises(up.UpdateError, match="unsafe path"):
            up.install(zip_path, root, "0.5.7")
        assert not (tmp_path / "evil.txt").exists()

    def test_installing_twice_is_a_no_op(self, tmp_path):
        root = tmp_path / "install"
        root.mkdir()
        zip_path = make_zip(tmp_path / "app-0.5.7.zip", "0.5.7")
        first = up.install(zip_path, root, "0.5.7")
        (first / "marker").write_text("untouched")

        again = up.install(zip_path, root, "0.5.7")

        assert again == first
        assert (first / "marker").exists()   # not re-extracted over the top


class TestNewestAndPrune:
    def _install(self, root: Path, *versions: str) -> None:
        for v in versions:
            d = root / f"app-{v}"
            d.mkdir(parents=True)
            (d / "EmptyOS.exe").write_bytes(b"MZ")
            (d / up.OK_MARKER).write_text(v)

    def test_newest_wins_numerically(self, tmp_path):
        self._install(tmp_path, "0.5.9", "0.5.10", "0.4.0")

        version, path = up.newest_version(tmp_path)

        assert version == "0.5.10"
        assert path == tmp_path / "app-0.5.10"

    def test_prune_keeps_two(self, tmp_path):
        self._install(tmp_path, "0.5.5", "0.5.6", "0.5.7", "0.5.8")

        removed = up.prune(tmp_path, keep=2)

        assert sorted(removed) == ["0.5.5", "0.5.6"]
        assert {v for v, _ in up.installed_versions(tmp_path)} == {"0.5.7", "0.5.8"}

    def test_prune_never_deletes_the_running_version(self, tmp_path):
        """Deleting the directory you are executing from is not survivable."""
        self._install(tmp_path, "0.5.5", "0.5.6", "0.5.7", "0.5.8")

        up.prune(tmp_path, keep=2, protect="0.5.5")

        assert (tmp_path / "app-0.5.5").exists()
        assert not (tmp_path / "app-0.5.6").exists()

    def test_prune_clears_stale_staging(self, tmp_path):
        self._install(tmp_path, "0.5.8")
        staging = tmp_path / "updates"
        staging.mkdir()
        (staging / "app-0.5.7.zip").write_bytes(b"old")     # version we no longer have
        (staging / "app-0.5.8.zip").write_bytes(b"live")    # version we're running
        (staging / "app-0.5.9.partial").write_bytes(b"..")  # a dead download

        up.prune(tmp_path, keep=2)

        assert not (staging / "app-0.5.7.zip").exists()
        assert not (staging / "app-0.5.9.partial").exists()
        assert (staging / "app-0.5.8.zip").exists()


class TestEndToEnd:
    def test_download_verify_install_switch(self, tmp_path):
        """The whole update, through the real code path, over file://."""
        root = tmp_path / "install"
        root.mkdir()
        # 0.5.6 is running.
        cur = root / "app-0.5.6"
        cur.mkdir()
        (cur / "EmptyOS.exe").write_bytes(b"MZ old")
        (cur / up.OK_MARKER).write_text("0.5.6")

        src = make_zip(tmp_path / "release.zip", "0.5.7")
        release = release_for(src, "0.5.7")

        app_dir = up.update_to(release, root)

        assert app_dir == root / "app-0.5.7"
        # The stub would now pick 0.5.7 — that *is* the switch. Nothing about the
        # running 0.5.6 was touched.
        assert up.newest_version(root)[0] == "0.5.7"
        assert (cur / "EmptyOS.exe").read_bytes() == b"MZ old"


class TestExitCodeContract:
    def test_update_exit_code_matches_the_settings_app(self):
        """The daemon exits 43 to say "restart into the update"; the launcher's
        supervisor is what reads it. If they disagree, Apply silently becomes Quit."""
        from _shared.launcher_core import RESTART_EXIT_CODE, UPDATE_EXIT_CODE

        assert UPDATE_EXIT_CODE == 43
        assert RESTART_EXIT_CODE != UPDATE_EXIT_CODE
