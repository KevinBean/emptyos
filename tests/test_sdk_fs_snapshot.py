"""SDK unit tests: fs_snapshot — kernel-free, no daemon required."""

import os
import shutil
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from emptyos.sdk import fs_snapshot


def _seed_vault(root: Path):
    (root / "10_Projects").mkdir(parents=True)
    (root / "10_Projects" / "note.md").write_text("hello", encoding="utf-8")
    (root / "daily.md").write_text("# day", encoding="utf-8")
    # Excluded dir (leading-dot) + excluded media file
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("x", encoding="utf-8")
    (root / "clip.mp4").write_bytes(b"\x00" * 1024)


# ── Incremental (default) mode ─────────────────────────────────

def test_incremental_default_first_run_copies_all(tmp_path):
    src = tmp_path / "vault"; src.mkdir(); _seed_vault(src)
    res = fs_snapshot.snapshot_tree(src, tmp_path / "backups")
    assert res["mode"] == "incremental"
    snap = Path(res["snapshot"])
    assert snap.is_dir()
    assert res["files"] == 2 and res["copied"] == 2 and res["linked"] == 0
    assert (snap / "10_Projects" / "note.md").exists()
    assert not (snap / ".git").exists() and not (snap / "clip.mp4").exists()


def test_incremental_hardlinks_unchanged_copies_changed(tmp_path):
    src = tmp_path / "vault"; src.mkdir()
    (src / "a.md").write_text("stable", encoding="utf-8")
    (src / "b.md").write_text("v1", encoding="utf-8")
    dest = tmp_path / "backups"; dest.mkdir()
    # Simulate a prior-day snapshot (copytree preserves mtimes via copy2).
    prior = dest / (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    shutil.copytree(src, prior)
    # b.md changes since the prior snapshot; a.md does not.
    (src / "b.md").write_text("v2-changed", encoding="utf-8")

    res = fs_snapshot.snapshot_tree(src, dest)  # today's incremental
    snap = Path(res["snapshot"])
    assert res["linked"] == 1 and res["copied"] == 1
    # a.md unchanged → hardlinked to the prior snapshot (same inode)
    assert os.path.samefile(snap / "a.md", prior / "a.md")
    # b.md changed → fresh copy (different inode), correct content
    assert not os.path.samefile(snap / "b.md", prior / "b.md")
    assert (snap / "b.md").read_text(encoding="utf-8") == "v2-changed"
    # added_bytes counts only the copied file
    assert res["added_bytes"] == len(b"v2-changed")


# ── Zip mode ───────────────────────────────────────────────────

def test_zip_mode_contents_and_excludes(tmp_path):
    src = tmp_path / "vault"; src.mkdir(); _seed_vault(src)
    res = fs_snapshot.snapshot_tree(src, tmp_path / "backups", mode="zip")
    assert res["mode"] == "zip" and res["compressed"] is True
    snap = Path(res["snapshot"]); assert snap.suffix == ".zip"
    with zipfile.ZipFile(snap) as zf:
        names = set(zf.namelist())
    assert "10_Projects/note.md" in names and "daily.md" in names
    assert not any(n.startswith(".git") for n in names)
    assert "clip.mp4" not in names


def test_zip_handles_pre_1980_mtime(tmp_path):
    src = tmp_path / "vault"; src.mkdir()
    (src / "normal.md").write_text("ok", encoding="utf-8")
    old = src / "ancient.md"; old.write_text("from before 1980", encoding="utf-8")
    os.utime(old, (0, 0))  # 1970-01-01 — pre-ZIP-epoch
    res = fs_snapshot.snapshot_tree(src, tmp_path / "backups", mode="zip")
    assert res["files"] == 2
    with zipfile.ZipFile(res["snapshot"]) as zf:
        assert zf.testzip() is None
        assert zf.read("ancient.md") == b"from before 1980"


# ── Folder mode ────────────────────────────────────────────────

def test_folder_mode_copies_tree(tmp_path):
    src = tmp_path / "vault"; src.mkdir(); _seed_vault(src)
    res = fs_snapshot.snapshot_tree(src, tmp_path / "backups", mode="folder")
    assert res["mode"] == "folder"
    snap = Path(res["snapshot"]); assert snap.is_dir()
    assert (snap / "10_Projects" / "note.md").exists()
    assert not (snap / ".git").exists()


def test_same_day_replace_clears_other_form(tmp_path):
    # A stale same-date .zip (e.g. from a failed zip run) must be removed when
    # an incremental/folder snapshot for the same day is created.
    src = tmp_path / "vault"; src.mkdir(); (src / "a.md").write_text("x")
    dest = tmp_path / "backups"; dest.mkdir()
    stamp = datetime.now().strftime("%Y-%m-%d")
    (dest / f"{stamp}.zip").write_bytes(b"partial-junk")   # stale failed-run zip
    fs_snapshot.snapshot_tree(src, dest, mode="incremental")
    assert not (dest / f"{stamp}.zip").exists()            # cleared
    assert (dest / stamp).is_dir()                         # new folder snapshot


def test_incremental_flags_hardlink_support(tmp_path):
    src = tmp_path / "vault"; src.mkdir()
    (src / "a.md").write_text("stable", encoding="utf-8")
    dest = tmp_path / "backups"; dest.mkdir()
    prior = dest / (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    shutil.copytree(src, prior)
    res = fs_snapshot.snapshot_tree(src, dest)  # tmp_path is hardlink-capable
    assert res["had_prior"] is True
    assert res["hardlink_supported"] is True and res["linked"] >= 1


# ── Restore ────────────────────────────────────────────────────

def test_restore_zip(tmp_path):
    src = tmp_path / "vault"; src.mkdir(); _seed_vault(src)
    res = fs_snapshot.snapshot_tree(src, tmp_path / "backups", mode="zip")
    out = tmp_path / "restored"
    r = fs_snapshot.restore_snapshot(Path(res["snapshot"]), out)
    assert r["files"] == 2
    assert (out / "10_Projects" / "note.md").read_text(encoding="utf-8") == "hello"
    assert (out / "daily.md").exists()


def test_restore_folder_derefs_hardlinks(tmp_path):
    src = tmp_path / "vault"; src.mkdir()
    (src / "a.md").write_text("stable", encoding="utf-8")
    dest = tmp_path / "backups"; dest.mkdir()
    prior = dest / (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    shutil.copytree(src, prior)
    snap = Path(fs_snapshot.snapshot_tree(src, dest)["snapshot"])  # a.md is hardlinked
    out = tmp_path / "restored"
    fs_snapshot.restore_snapshot(snap, out)
    # restored copy is independent — NOT sharing the snapshot's inode
    assert (out / "a.md").read_text(encoding="utf-8") == "stable"
    assert not os.path.samefile(out / "a.md", snap / "a.md")


def test_restore_missing_raises(tmp_path):
    try:
        fs_snapshot.restore_snapshot(tmp_path / "nope", tmp_path / "out")
        assert False, "expected FileNotFoundError"
    except FileNotFoundError:
        pass


def test_invalid_mode_raises(tmp_path):
    src = tmp_path / "vault"; src.mkdir(); (src / "a.md").write_text("x")
    try:
        fs_snapshot.snapshot_tree(src, tmp_path / "b", mode="nope")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_missing_source_raises(tmp_path):
    try:
        fs_snapshot.snapshot_tree(tmp_path / "nope", tmp_path / "out")
        assert False, "expected FileNotFoundError"
    except FileNotFoundError:
        pass


# ── Prune + list across forms ──────────────────────────────────

def test_prune_removes_old_zip_and_folder(tmp_path):
    dest = tmp_path / "backups"; dest.mkdir()
    old_day = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    recent_day = datetime.now().strftime("%Y-%m-%d")
    (dest / f"{old_day}.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    (dest / old_day).mkdir()
    (dest / f"{recent_day}.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    (dest / "manual-keep").mkdir()
    removed = fs_snapshot.prune_snapshots(dest, retention_days=7)
    assert f"{old_day}.zip" in removed and old_day in removed
    assert not (dest / f"{old_day}.zip").exists() and not (dest / old_day).exists()
    assert (dest / f"{recent_day}.zip").exists()
    assert (dest / "manual-keep").exists()


def test_list_snapshots_newest_first_with_size(tmp_path):
    dest = tmp_path / "backups"; dest.mkdir()
    (dest / "2026-05-01.zip").write_bytes(b"x" * 100)
    (dest / "2026-05-03.zip").write_bytes(b"x" * 100)
    (dest / "2026-05-02").mkdir()
    (dest / "not-a-date").mkdir()
    snaps = fs_snapshot.list_snapshots(dest)
    assert [s["name"] for s in snaps] == ["2026-05-03.zip", "2026-05-02", "2026-05-01.zip"]
    by_name = {s["name"]: s for s in snaps}
    assert by_name["2026-05-03.zip"]["compressed"] is True
    assert by_name["2026-05-02"]["compressed"] is False


# ── Exclude configuration (site-packages, custom, opt-in media) ─────

def test_site_packages_and_compiled_binaries_excluded_by_default(tmp_path):
    """Bundled envs outside a venv/ dir + compiled/platform binaries are junk;
    they must never land in a snapshot (the 22k-file Software-Tools gap)."""
    src = tmp_path / "vault"; src.mkdir()
    (src / "note.md").write_text("keep", encoding="utf-8")
    sp = src / "Tools" / "site-packages" / "numpy"; sp.mkdir(parents=True)
    (sp / "core.py").write_text("junk", encoding="utf-8")
    (src / "build.pyc").write_bytes(b"\x00")
    (src / "lib.dll").write_bytes(b"\x00")
    res = fs_snapshot.snapshot_tree(src, tmp_path / "backups", mode="folder")
    snap = Path(res["snapshot"])
    assert (snap / "note.md").exists()
    assert not (snap / "Tools" / "site-packages").exists()
    assert not (snap / "build.pyc").exists() and not (snap / "lib.dll").exists()
    assert res["files"] == 1


def test_custom_exclude_dirs_and_suffixes(tmp_path):
    src = tmp_path / "vault"; src.mkdir()
    (src / "keep.md").write_text("k", encoding="utf-8")
    (src / "scratch").mkdir(); (src / "scratch" / "x.md").write_text("drop", encoding="utf-8")
    (src / "big.iso").write_bytes(b"\x00" * 10)
    res = fs_snapshot.snapshot_tree(
        src, tmp_path / "backups", mode="folder",
        exclude_dirs=fs_snapshot.DEFAULT_EXCLUDE_DIRS | {"scratch"},
        exclude_suffixes=fs_snapshot.DEFAULT_EXCLUDE_SUFFIXES | {".iso"})
    snap = Path(res["snapshot"])
    assert (snap / "keep.md").exists()
    assert not (snap / "scratch").exists() and not (snap / "big.iso").exists()


def test_media_included_when_suffix_dropped(tmp_path):
    """The app's include_media toggle = defaults minus MEDIA_SUFFIXES."""
    src = tmp_path / "vault"; src.mkdir()
    (src / "note.md").write_text("k", encoding="utf-8")
    (src / "clip.mp4").write_bytes(b"\x00" * 32)
    (src / "song.flac").write_bytes(b"\x00" * 16)
    keep_media = fs_snapshot.DEFAULT_EXCLUDE_SUFFIXES - fs_snapshot.MEDIA_SUFFIXES
    res = fs_snapshot.snapshot_tree(
        src, tmp_path / "backups", mode="folder", exclude_suffixes=keep_media)
    snap = Path(res["snapshot"])
    assert (snap / "clip.mp4").exists() and (snap / "song.flac").exists()
    assert res["files"] == 3
