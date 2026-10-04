"""Migrate saved board configs to the latest preset schema.

Safe-merge strategy:
  * Walks every saved board JSON under `{vault}/30_Resources/EmptyOS/boards/`.
  * If its `id` matches a preset, appends any NEW columns the preset defines.
  * Existing columns are left untouched — user customizations win.
  * `rules`, `views` unchanged unless a preset adds a brand-new one by key.

Run after editing `apps/boards/presets.py`. Does NOT need the daemon running.
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path


def _load_vault_path() -> Path:
    cfg_path = Path(__file__).resolve().parent.parent / "emptyos.toml"
    with open(cfg_path, "rb") as f:
        cfg = tomllib.load(f)
    return Path(cfg["notes"]["path"])


def main():
    # Import presets after we're in the repo, not via the daemon.
    repo = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo))
    from apps.boards.presets import PRESETS

    vault = _load_vault_path()
    boards_dir = vault / "30_Resources/EmptyOS/boards"
    if not boards_dir.exists():
        print(f"No boards dir at {boards_dir} — nothing to migrate.")
        return

    # Preload every saved config so we can cross-reference (e.g., point
    # project-tracker.deliverables at whatever deliverables board the user
    # actually has in the vault).
    saved: dict[str, dict] = {}
    for fp in sorted(boards_dir.glob("*.json")):
        if fp.name.startswith("_"):
            continue
        try:
            saved[fp.stem] = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Detect "EPC deliverables-style" custom boards — has designer + checker
    # + approver columns, no preset match. We want to treat these as
    # deliverables boards for rollup purposes.
    def _is_epc_deliverables(cfg: dict) -> bool:
        types_by_id = {c.get("id"): c.get("type") for c in cfg.get("columns", [])}
        return (
            types_by_id.get("designer") == "designer"
            and types_by_id.get("checker") == "checker"
            and types_by_id.get("approver") == "approver"
        )

    # Decide the canonical deliverables board id. Prefer one the user already
    # has saved; fall back to the preset blueprint.
    epc_boards = [
        bid for bid, cfg in saved.items() if bid != "project-tracker" and _is_epc_deliverables(cfg)
    ]
    deliverables_board = epc_boards[0] if epc_boards else "engineering-deliverables"
    if epc_boards:
        print(f"Detected EPC deliverables board(s): {epc_boards}")
        print(f"  project-tracker.deliverables will point at: {deliverables_board}")

    touched = 0
    skipped = 0
    for fp in sorted(boards_dir.glob("*.json")):
        if fp.name.startswith("_"):
            continue
        bid = fp.stem
        cfg = saved.get(bid)
        if cfg is None:
            print(f"  skip (unreadable): {fp.name}")
            skipped += 1
            continue

        changed = False
        preset = PRESETS.get(bid)

        if preset:
            existing_ids = {c.get("id") for c in cfg.get("columns", []) if c.get("id")}
            new_cols = [
                dict(c) for c in preset.get("columns", []) if c.get("id") not in existing_ids
            ]
            # Retarget project-tracker.deliverables onto the user's real board.
            if bid == "project-tracker":
                for c in cfg.get("columns", []) + new_cols:
                    if c.get("id") == "deliverables" and c.get("type") == "link-record":
                        if c.get("target_board") != deliverables_board:
                            c["target_board"] = deliverables_board
                            changed = True
            if new_cols:
                cfg.setdefault("columns", []).extend(new_cols)
                added = ", ".join(c["id"] for c in new_cols)
                print(f"  migrated: {bid}  +{len(new_cols)} cols  [{added}]")
                changed = True

        elif _is_epc_deliverables(cfg):
            # Custom EPC-pattern board — add the per-item + parent-link + formula
            # columns from the engineering-deliverables preset so rollups work.
            epc = PRESETS.get("engineering-deliverables", {})
            existing_ids = {c.get("id") for c in cfg.get("columns", []) if c.get("id")}
            # Only add the fields that enable rollups / linking upstream.
            wanted = {"est_hours", "progress", "parent_project", "overdue"}
            new_cols = [
                dict(c)
                for c in epc.get("columns", [])
                if c.get("id") in wanted and c.get("id") not in existing_ids
            ]
            # Repoint the parent_project link at project-tracker (preset default).
            for c in new_cols:
                if c.get("id") == "parent_project":
                    c["target_board"] = "project-tracker"
            if new_cols:
                cfg.setdefault("columns", []).extend(new_cols)
                added = ", ".join(c["id"] for c in new_cols)
                print(f"  migrated (EPC-style): {bid}  +{len(new_cols)} cols  [{added}]")
                changed = True

        if not changed:
            print(f"  up-to-date: {bid}")
            skipped += 1
            continue

        fp.write_text(
            json.dumps(cfg, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        touched += 1

    print(f"\n{touched} board(s) migrated, {skipped} unchanged.")
    if touched:
        print(
            "Restart the daemon (or POST /boards/api/links/rebuild) "
            "to pick up new link-record columns in the in-memory index."
        )


if __name__ == "__main__":
    main()
