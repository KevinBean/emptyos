#!/usr/bin/env python3
"""fileindex_scan.py — drive-wide knowledge-corpus index ledger.

Tracks which files on a local drive have had their knowledge extracted into
the vault. Five states: 未索引 / 部分索引 / 完全索引 / 排除 / 待定.
部分索引 and 排除 require a note (what was extracted / why excluded).

Generic scanner — all personal paths and rules live in a TOML config stored
IN THE VAULT (hand-authored judgment = recovery-critical), default:
    {vault}/10_Projects/file-indexing/fileindex.toml
resolved via emptyos.toml [notes].path. The SQLite DB is regenerable machine
state (data/fileindex/index.db). Judgments are exported to the vault ledger
(ledger.jsonl) so the whole system is recoverable from vault + rescan:

    rescan + import-ledger == full state

Commands:
  scan                          walk roots, upsert (never clobbers judgments)
  summary [--json]              counts by corpus × status
  queue [-n 20] [--corpus X] [--ext e1,e2] [--json]
                                highest-priority 未索引 files (the mining queue)
  status <substr> --set <状态> [--note N] [--vault-ref V] [--all]
         [--corpus X] [--dry-run] [--cross-corpus] [--force]
         NOTE: substring match is case-insensitive and spans ALL corpora.
         ALWAYS --dry-run first; scope with --corpus; a short/common token
         ("Model","图") will clobber thousands. Cross-corpus + 完全/排除
         overwrites are REFUSED unless explicitly forced.
                                judge file(s) matched by path substring
  report                        markdown dashboard (paste into vault note)
  export                        judgments → {vault}/.../ledger.jsonl
  import-ledger                 replay vault ledger into DB
  dups [--corpus X] [-n 30]     same-basename duplicate clusters (dedupe aid)

Pure stdlib. Never imports the EmptyOS kernel (daemon-handling safe).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")  # cp1252 console trap

STATUSES = ("未索引", "部分索引", "完全索引", "排除", "待定")
NOTE_REQUIRED = ("部分索引", "排除")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "data" / "fileindex" / "index.db"


# ── config ────────────────────────────────────────────────────────────────

def vault_root() -> Path | None:
    toml_path = REPO_ROOT / "emptyos.toml"
    if not toml_path.exists():
        return None
    with open(toml_path, "rb") as f:
        cfg = tomllib.load(f)
    p = (cfg.get("notes") or {}).get("path", "")
    return Path(p) if p else None


def default_config_path() -> Path | None:
    v = vault_root()
    return v / "10_Projects" / "file-indexing" / "fileindex.toml" if v else None


def load_config(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


# ── db ────────────────────────────────────────────────────────────────────

def open_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("""CREATE TABLE IF NOT EXISTS files(
        path TEXT PRIMARY KEY, corpus TEXT, ext TEXT,
        size INTEGER, mtime REAL, class TEXT, priority REAL,
        status TEXT DEFAULT '未索引', note TEXT DEFAULT '',
        vault_ref TEXT DEFAULT '', sha256 TEXT DEFAULT '',
        last_seen TEXT, updated TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS dirs(
        path TEXT PRIMARY KEY, corpus TEXT, file_count INTEGER,
        bytes INTEGER, class TEXT, status TEXT, reason TEXT, last_seen TEXT)""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_files_status ON files(status)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_files_corpus ON files(corpus)")
    return con


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── scan ──────────────────────────────────────────────────────────────────

def iter_entries(root: str):
    """Yield (path, size, mtime, is_dir) via scandir; swallow access errors."""
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                    elif e.is_file(follow_symlinks=False):
                        st = e.stat()
                        yield e.path, st.st_size, st.st_mtime
                except OSError:
                    continue


def priority_score(path: str, ext: str, cfg: dict, tier: float) -> float:
    w = cfg.get("weights", {})
    ext_w = w.get("ext", {}).get(ext, 1.0)
    score = float(ext_w)
    boosts = w.get("boost_keywords", [])
    hits = sum(1 for kw in boosts if kw in path)
    score += min(hits * 5.0, 10.0)
    for kw in w.get("penalty_keywords", []):
        if kw in path:
            score -= 4.0
    return round(score * tier, 2)


def cmd_scan(con: sqlite3.Connection, cfg: dict) -> None:
    t0 = time.time()
    roots = cfg.get("general", {}).get("roots", ["D:/"])
    exclude = {e["name"]: e.get("reason", "") for e in cfg.get("exclude", [])}
    # sub-path exclusions: substring match against the normalised full path.
    # These are noise *inside* an otherwise-valuable corpus (vendored code,
    # site backups, app-internal asset dirs, blank form templates).
    ex_paths = [(e["match"], e.get("reason", "")) for e in cfg.get("exclude_path", [])]
    ex_names = {e["name"]: e.get("reason", "") for e in cfg.get("exclude_file", [])}
    corpora = cfg.get("corpus", {})
    know_ext = set(cfg.get("weights", {}).get("ext", {}))
    ts = now()

    n_files = n_bulk = 0
    for root in roots:
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            print(f"!! cannot list {root}")
            continue
        for name in entries:
            top = os.path.join(root, name)
            if not os.path.isdir(top):
                continue  # loose top-level files: ignore (rare, add corpus if needed)
            if name in exclude:
                cnt = b = 0
                for _, sz, _ in iter_entries(top):
                    cnt += 1
                    b += sz
                con.execute(
                    """INSERT INTO dirs(path,corpus,file_count,bytes,class,status,reason,last_seen)
                       VALUES(?,?,?,?,?,?,?,?)
                       ON CONFLICT(path) DO UPDATE SET file_count=excluded.file_count,
                         bytes=excluded.bytes, last_seen=excluded.last_seen,
                         status=excluded.status, reason=excluded.reason""",
                    (top.replace("\\", "/"), "", cnt, b, "excluded", "排除",
                     exclude[name], ts))
                print(f"  排除  {name:<40} {cnt:>8,} files  {b/1e9:7.1f} GB  ({exclude[name]})")
                continue
            if name not in corpora:
                cnt = sum(1 for _ in iter_entries(top))
                con.execute(
                    """INSERT INTO dirs(path,corpus,file_count,bytes,class,status,reason,last_seen)
                       VALUES(?,?,?,?,?,?,?,?)
                       ON CONFLICT(path) DO UPDATE SET file_count=excluded.file_count,
                         last_seen=excluded.last_seen""",
                    (top.replace("\\", "/"), "", cnt, 0, "unknown", "待定",
                     "未分类的顶层目录 — 在 fileindex.toml 中归入 corpus 或 exclude", ts))
                print(f"  待定  {name:<40} {cnt:>8,} files  (未分类)")
                continue

            spec = corpora[name]
            tier = float(spec.get("tier", 1.0))
            bulk: dict[tuple[str, str], list[int]] = {}
            skipped: dict[str, int] = {}
            rows = []
            for p, sz, mt in iter_entries(top):
                fn = os.path.basename(p)
                if fn.startswith("._") or fn.startswith("~$"):
                    continue
                ext = fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
                rel = p.replace("\\", "/")
                hit = next((m for m, _ in ex_paths if m in rel), None)
                if hit is None and fn.lower() in ex_names:
                    hit = fn.lower()
                if hit is not None:
                    skipped[hit] = skipped.get(hit, 0) + 1
                    con.execute("DELETE FROM files WHERE path=? AND status='未索引'", (rel,))
                    continue
                if ext in know_ext:
                    rows.append((rel, name, ext, sz, mt, "knowledge",
                                 priority_score(rel, ext, cfg, tier), ts))
                else:
                    parts = rel.split("/")
                    d2 = "/".join(parts[:3]) if len(parts) > 3 else "/".join(parts[:-1])
                    k = (d2, "bulk")
                    bulk.setdefault(k, [0, 0])
                    bulk[k][0] += 1
                    bulk[k][1] += sz
            con.executemany(
                """INSERT INTO files(path,corpus,ext,size,mtime,class,priority,last_seen,updated)
                   VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime,
                     priority=excluded.priority, corpus=excluded.corpus,
                     last_seen=excluded.last_seen""",
                [(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[7]) for r in rows])
            for (d2, cls), (cnt, b) in bulk.items():
                con.execute(
                    """INSERT INTO dirs(path,corpus,file_count,bytes,class,status,reason,last_seen)
                       VALUES(?,?,?,?,?,?,?,?)
                       ON CONFLICT(path) DO UPDATE SET file_count=excluded.file_count,
                         bytes=excluded.bytes, last_seen=excluded.last_seen""",
                    (d2, name, cnt, b, cls, "未索引",
                     "批量类(媒体/数据/其他) — 目录级追踪", ts))
            reasons = dict(ex_paths) | ex_names
            for m, cnt in skipped.items():
                con.execute(
                    """INSERT INTO dirs(path,corpus,file_count,bytes,class,status,reason,last_seen)
                       VALUES(?,?,?,?,?,?,?,?)
                       ON CONFLICT(path) DO UPDATE SET file_count=excluded.file_count,
                         last_seen=excluded.last_seen, reason=excluded.reason""",
                    (f"{top.replace(chr(92),'/')}  [子路径排除: {m}]", name, cnt, 0,
                     "excluded", "排除", reasons.get(m, ""), ts))
            n_files += len(rows)
            n_bulk += len(bulk)
            skip_n = sum(skipped.values())
            extra = f", 子路径排除 {skip_n:,}" if skip_n else ""
            print(f"  语料  {name:<40} {len(rows):>8,} knowledge files  ({len(bulk)} bulk dirs{extra})")
    con.commit()
    print(f"\nscan done in {time.time()-t0:.0f}s — {n_files:,} knowledge files, {n_bulk:,} bulk dir rows")


# ── judgments ─────────────────────────────────────────────────────────────

def cmd_status(con, args) -> None:
    if args.set not in STATUSES:
        sys.exit(f"status must be one of {STATUSES}")
    if args.set in NOTE_REQUIRED and not args.note:
        sys.exit(f"'{args.set}' requires --note (what was extracted / why excluded)")
    # LIKE is case-insensitive for ASCII and matches ALL corpora — a short/common
    # substring ("Model", "图") silently clobbers thousands of files across every
    # corpus. Guard: preview by-corpus + by-current-status, warn on 完全/排除
    # overwrites, refuse a cross-corpus write unless explicitly allowed.
    pat = f"%{args.substr}%"
    where = "path LIKE ?"
    params: list = [pat]
    if args.corpus:
        where += " AND corpus=?"
        params.append(args.corpus)
    rows = con.execute(
        f"SELECT path, corpus, status FROM files WHERE {where}", params).fetchall()
    if not rows:
        sys.exit(f"no files match substring: {args.substr}"
                 + (f" in corpus {args.corpus}" if args.corpus else ""))

    from collections import Counter
    by_corpus = Counter(r[1] for r in rows)
    by_status = Counter(r[2] for r in rows)
    overwrite_high = [r for r in rows if r[2] in ("完全索引", "排除")]

    def _preview(prefix: str) -> None:
        print(f"{prefix}: {len(rows)} file(s) match '{args.substr}'"
              + (f" in {args.corpus}" if args.corpus else " across ALL corpora"))
        print("  by corpus:  " + ", ".join(f"{c}={n}" for c, n in by_corpus.most_common()))
        print("  currently:  " + ", ".join(f"{s}={n}" for s, n in by_status.most_common()))
        if overwrite_high:
            print(f"  ⚠ {len(overwrite_high)} already 完全索引/排除 would be OVERWRITTEN:")
            for p, c, s in overwrite_high[:12]:
                print(f"      [{s}] {p}")
        for p, c, s in rows[:8]:
            print("   sample:", p)

    if args.dry_run:
        _preview("DRY-RUN")
        print("(no changes written)")
        return

    # Refuse an un-narrowed multi-file write unless --all
    if len(rows) > 1 and not args.all:
        _preview("MATCH")
        print("pass --all to update all, or narrow the substring / add --corpus")
        sys.exit(1)

    # Refuse a cross-corpus write unless the operator explicitly allows it
    if len(by_corpus) > 1 and not args.corpus and not args.cross_corpus:
        _preview("CROSS-CORPUS")
        print("REFUSED: substring matches multiple corpora. Add --corpus <name> to scope,"
              " or --cross-corpus to override (rarely correct).")
        sys.exit(2)

    # --only-unindexed: coverage-marking mode — mark ONLY 未索引 matches, leaving
    # every existing judgment (部分/完全/排除) untouched. Safest blanket-cover: it
    # can never clobber a specific note already written on a 部分索引 file.
    if args.only_unindexed:
        n_target = by_status.get("未索引", 0)
        if n_target == 0:
            print(f"nothing to do: none of the {len(rows)} matches are 未索引 (all already judged)")
            return
        where += " AND status='未索引'"
        con.execute(
            f"UPDATE files SET status=?, note=?, vault_ref=?, updated=? WHERE {where}",
            [args.set, args.note or "", args.vault_ref or "", now()] + params)
        con.commit()
        already = len(rows) - n_target
        print(f"updated {n_target} 未索引 file(s) → {args.set}"
              + (f" in {args.corpus}" if args.corpus else "")
              + (f" (left {already} already-judged untouched)" if already else ""))
        return

    # --skip-completed: coverage-marking mode — mark the unjudged/partial matches
    # but never touch 完全索引/排除 (protects note-sources). This is the safe way
    # to blanket-cover a project folder that has digested sources mixed in.
    if args.skip_completed and overwrite_high:
        where += " AND status NOT IN ('完全索引','排除')"
        n_target = len(rows) - len(overwrite_high)
        if n_target == 0:
            print(f"nothing to do: all {len(rows)} matches are already 完全索引/排除 (skipped)")
            return
    else:
        # Loud confirmation when overwriting completed/excluded judgments
        if overwrite_high and not args.force:
            _preview("OVERWRITE-GUARD")
            print(f"REFUSED: would overwrite {len(overwrite_high)} 完全索引/排除 judgment(s)."
                  " Re-run with --skip-completed to mark only the rest, or --force to overwrite.")
            sys.exit(3)
        n_target = len(rows)

    con.execute(
        f"UPDATE files SET status=?, note=?, vault_ref=?, updated=? WHERE {where}",
        [args.set, args.note or "", args.vault_ref or "", now()] + params)
    con.commit()
    skipped = f" (skipped {len(overwrite_high)} 完全索引/排除)" if (args.skip_completed and overwrite_high) else ""
    print(f"updated {n_target} file(s) → {args.set}"
          + (f" in {args.corpus}" if args.corpus else "") + skipped)


def cmd_queue(con, args) -> None:
    q = "SELECT path, priority, size, ext FROM files WHERE status='未索引'"
    params: list = []
    if args.corpus:
        q += " AND corpus=?"
        params.append(args.corpus)
    if args.ext:
        exts = args.ext.split(",")
        q += f" AND ext IN ({','.join('?'*len(exts))})"
        params += exts
    q += " ORDER BY priority DESC, size DESC LIMIT ?"
    params.append(args.n)
    rows = con.execute(q, params).fetchall()
    if args.json:
        print(json.dumps({"ok": True, "data": [
            {"path": p, "priority": pr, "size": s, "ext": e} for p, pr, s, e in rows
        ]}, ensure_ascii=False))
        return
    for p, pr, s, e in rows:
        print(f"{pr:6.1f}  {s/1e6:8.1f} MB  {p}")


def cmd_summary(con, args) -> None:
    rows = con.execute("""SELECT corpus, status, COUNT(*), SUM(size)
                          FROM files GROUP BY corpus, status ORDER BY corpus""").fetchall()
    if args.json:
        print(json.dumps({"ok": True, "data": [
            {"corpus": c, "status": st, "count": n, "bytes": b or 0}
            for c, st, n, b in rows]}, ensure_ascii=False))
        return
    for c, st, n, b in rows:
        print(f"  {c:<45} {st:<6} {n:>8,}  {(b or 0)/1e9:7.2f} GB")


def cmd_report(con, cfg) -> None:
    print("## 状态仪表盘\n")
    print(f"更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    print("### 知识语料 (逐文件)\n")
    print("| 语料库 | 未索引 | 部分索引 | 完全索引 | 排除 | 待定 | 合计 |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    corp = {}
    for c, st, n in con.execute(
            "SELECT corpus, status, COUNT(*) FROM files GROUP BY corpus, status"):
        corp.setdefault(c, {})[st] = n
    for c in sorted(corp):
        d = corp[c]
        tot = sum(d.values())
        print(f"| {c} | {d.get('未索引',0):,} | {d.get('部分索引',0):,} "
              f"| {d.get('完全索引',0):,} | {d.get('排除',0):,} | {d.get('待定',0):,} | {tot:,} |")
    print("\n### 排除/批量目录 (目录级)\n")
    print("| 目录 | 文件数 | 大小 | 状态 | 理由 |")
    print("|---|---:|---:|---|---|")
    for p, n, b, st, r in con.execute(
            """SELECT path, file_count, bytes, status, reason FROM dirs
               WHERE class IN ('excluded','unknown') ORDER BY file_count DESC"""):
        print(f"| {p} | {n:,} | {(b or 0)/1e9:.1f} GB | {st} | {r} |")


def cmd_dups(con, args) -> None:
    rows = con.execute(
        "SELECT path FROM files" + (" WHERE corpus=?" if args.corpus else ""),
        ((args.corpus,) if args.corpus else ())).fetchall()
    clusters: dict[str, list] = {}
    for (p,) in rows:
        base = re.sub(r"[ _\-]*(副本|copy|Copy)?[ _\-]*\d*\.", ".", os.path.basename(p), count=1)
        clusters.setdefault(base.lower(), []).append(p)
    dup = sorted(((k, v) for k, v in clusters.items() if len(v) > 1),
                 key=lambda kv: -len(kv[1]))[:args.n]
    for k, v in dup:
        print(f"{len(v):>3}×  {k}")
        for p in v[:4]:
            print("      ", p)


# ── vault ledger ──────────────────────────────────────────────────────────

def ledger_path() -> Path:
    v = vault_root()
    if not v:
        sys.exit("cannot resolve vault root from emptyos.toml")
    return v / "10_Projects" / "file-indexing" / "ledger.jsonl"


# The ledger format is the durable recovery contract ("rescan + import-ledger
# == full state") and has TWO producers: this CLI and the fileindex app's
# `_export_ledger`. Both call a pure ledger_lines(); tests/personal/
# test_fileindex_ledger.py runs both against one fixture DB and asserts the
# output is byte-identical, so a change here can't silently orphan the app's.
LEDGER_SELECT = (
    "SELECT path, status, note, vault_ref, updated FROM files "
    "WHERE status != '未索引' OR note != '' OR vault_ref != '' "
    "ORDER BY path"
)


def ledger_lines(con) -> list[str]:
    """Serialise every judgment to JSONL. Pure — no I/O, no config."""
    return [
        json.dumps({"path": p, "status": st, "note": note,
                    "vault_ref": vr, "updated": up}, ensure_ascii=False)
        for p, st, note, vr, up in con.execute(LEDGER_SELECT)
    ]


def cmd_export(con) -> None:
    lp = ledger_path()
    lp.parent.mkdir(parents=True, exist_ok=True)
    lines = ledger_lines(con)
    lp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"exported {len(lines)} judgment(s) → {lp}")


def cmd_import(con) -> None:
    lp = ledger_path()
    if not lp.exists():
        sys.exit(f"no ledger at {lp}")
    n = 0
    with open(lp, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            n += con.execute(
                """UPDATE files SET status=?, note=?, vault_ref=?, updated=?
                   WHERE path=?""",
                (r["status"], r.get("note", ""), r.get("vault_ref", ""),
                 r.get("updated", now()), r["path"])).rowcount
    con.commit()
    print(f"replayed {n} judgment(s) from {lp}")


# ── main ──────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan")
    s = sub.add_parser("summary")
    s.add_argument("--json", action="store_true")
    q = sub.add_parser("queue")
    q.add_argument("-n", type=int, default=20)
    q.add_argument("--corpus")
    q.add_argument("--ext")
    q.add_argument("--json", action="store_true")
    st = sub.add_parser("status")
    st.add_argument("substr")
    st.add_argument("--set", required=True)
    st.add_argument("--note")
    st.add_argument("--vault-ref")
    st.add_argument("--all", action="store_true")
    st.add_argument("--corpus", help="scope the substring match to one corpus (STRONGLY recommended)")
    st.add_argument("--dry-run", action="store_true", help="preview matches (by corpus + current status) without writing")
    st.add_argument("--cross-corpus", action="store_true", help="allow a write whose substring spans multiple corpora (rarely correct)")
    st.add_argument("--force", action="store_true", help="allow overwriting existing 完全索引/排除 judgments")
    st.add_argument("--skip-completed", action="store_true", help="coverage mode: mark only unjudged/partial matches, never touch 完全索引/排除 sources")
    st.add_argument("--only-unindexed", action="store_true", help="coverage mode: mark ONLY 未索引 matches, leave all existing judgments (部分/完全/排除) untouched")
    sub.add_parser("report")
    sub.add_parser("export")
    sub.add_parser("import-ledger")
    d = sub.add_parser("dups")
    d.add_argument("--corpus")
    d.add_argument("-n", type=int, default=30)
    args = ap.parse_args()

    cfg_path = args.config or default_config_path()
    if not cfg_path or not cfg_path.exists():
        sys.exit(f"config not found: {cfg_path} — create fileindex.toml in the vault project dir")
    cfg = load_config(cfg_path)
    con = open_db(args.db)

    if args.cmd == "scan":
        cmd_scan(con, cfg)
    elif args.cmd == "summary":
        cmd_summary(con, args)
    elif args.cmd == "queue":
        cmd_queue(con, args)
    elif args.cmd == "status":
        cmd_status(con, args)
    elif args.cmd == "report":
        cmd_report(con, cfg)
    elif args.cmd == "export":
        cmd_export(con)
    elif args.cmd == "import-ledger":
        cmd_import(con)
    elif args.cmd == "dups":
        cmd_dups(con, args)


if __name__ == "__main__":
    main()
