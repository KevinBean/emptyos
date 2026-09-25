"""Unit pins for scripts/mv_library.py + scripts/check_mv_library.py.

Daemon-free and vault-free. The vocabulary fixtures below reproduce the markdown
shapes of the real library notes (``SCHEMA.md`` / ``failure-codes.md`` in the
vault's ``10_Projects/YouTube-Music-Channel/library/``) that the parser has to
survive, each of which broke a draft:

* §5's table header is itself backticked (`` | `platform` | `model` | ``);
* the asset status line carries ``（`status_reason` 必填）`` in a parenthetical;
* §6 shows an example pattern note inside a code fence, and that example has
  its own ``## `` headings — they must not end §6;
* §4 is followed by prose that mentions other backticked words.

Temp dirs come from ``tempfile`` rather than ``tmp_path`` (``pytest-current``
access is denied on the homepc box).
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


lib = _load("mv_library")
chk = _load("check_mv_library")

SCHEMA = """---
title: MV 庫格式
---

# MV 庫格式 v1

## 1. 生成記錄 `attempts.jsonl`

| 欄位 | 必填 | 說明 |
|---|---|---|
| `attempt_id` | 是 | x |

## 2. 素材記錄 `assets.jsonl`

`kind`：`scene-master` · `empty-plate` · `start-frame`

`status`：`approved` · `candidate` · `rejected` · `superseded`（`status_reason` 必填）

`reusable`：`general`（可跨專案用，如 UI 貼圖、LUT 方法）· `style-family`（同系列可沿用）· `scene-specific`

## 3. 結論詞表 `verdict`

| 值 | 意思 |
|---|---|
| `pass` | 可用 |
| `partial` | 部分 |
| `fail` | 不通過 |
| `rejected-human` | 否決 |
| `superseded` | 取代 |
| `generation-failed` | 失敗 |
| `unreviewed` | 未審 |

## 4. 階段 `stage`

`reference`（主景）· `still`（首幀）· `video` · `post`

Do not confuse a stage with a `mode` or a `kind`.

## 5. 平台與模型詞表

| `platform` | `model` | 備註 |
|---|---|---|
| `google-flow` | `nano-banana-pro`, `veo-3.1-fast` | x |
| `comfyui` | `wan-2.2-i2v` | x |

`mode`：`text-to-image` · `frames-start` · `image-edit`

## 6. 提示詞模式筆記 `patterns/<pattern_id>.md`

```markdown
---
status: tentative        # proven | tentative | deprecated
---

# 閉唇表演

## 用途
## 模板
```

`status: proven` 需要至少兩個專案的 `pass` 證據。

## 7. 回補對照
"""

CODES = """# MV 失敗代碼表

## A. 美術方向

| 代碼 | 意思 | art_ledger 次數 |
|---|---|---|
| `subject_scale_mismatch` | x | 125 |

## B. 表演

| 代碼 | 意思 | 首個證據 |
|---|---|---|
| `mouth_open` | x | y |
| `push_accelerates` | x | y |

## F. 流程與記錄

| 代碼 | 意思 |
|---|---|
| `prompt_not_persisted` | x |
"""

H64 = "a" * 64
H64B = "b" * 64


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as d:
        vault = Path(d) / "vault"
        library = vault / "lib"
        library.mkdir(parents=True)
        (library / "SCHEMA.md").write_text(SCHEMA, encoding="utf-8")
        (library / "failure-codes.md").write_text(CODES, encoding="utf-8")
        (vault / "proj").mkdir()
        for name in ("in.jpeg", "out.mp4", "review.md", "ledger.jsonl", "a.jpeg"):
            (vault / "proj" / name).write_text("x", encoding="utf-8")
        (Path(d) / "outside.txt").write_text("x", encoding="utf-8")
        yield vault, library


@pytest.fixture
def vocab():
    return lib.parse_vocab(SCHEMA, CODES)


def attempt(**over) -> dict:
    row = {
        "attempt_id": "P:video:S1:1", "project": "P", "project_path": "proj",
        "shot": "S1", "stage": "video", "attempt": 1, "platform": "google-flow",
        "model": "veo-3.1-fast", "mode": "frames-start",
        "inputs": [{"role": "start", "sha256": H64, "path": "proj/in.jpeg"}],
        "prompt": "slow push toward her", "credits": {"quoted": 10, "charged": 10},
        "output": {"sha256": H64B, "path": "proj/out.mp4"},
        "verdict": "partial", "codes": ["mouth_open"], "reason": "0-1.7 s usable",
        "evidence": {"path": "proj/review.md", "frames": "44-192"},
        "source": {"file": "proj/ledger.jsonl", "line": 3}, "recorded_by": "backfill",
    }
    row.update(over)
    return row


def asset(**over) -> dict:
    row = {
        "asset_id": "P:A1", "kind": "scene-master", "path": "proj/a.jpeg", "sha256": H64,
        "origin": {"platform": "google-flow", "model": "nano-banana-pro"},
        "rights": {"watermark": "synthid"}, "status": "approved", "status_reason": "clean",
        "reusable": "scene-specific", "parents": [],
    }
    row.update(over)
    return row


def codes_of(findings) -> list[str]:
    return [c for c, _ in findings]


# ── vocabulary ───────────────────────────────────────────────────────────────

def test_vocab_parses_every_family(vocab):
    assert vocab["verdict"] == {"pass", "partial", "fail", "rejected-human", "superseded",
                                "generation-failed", "unreviewed"}
    assert vocab["platform"] == {"google-flow": {"nano-banana-pro", "veo-3.1-fast"},
                                 "comfyui": {"wan-2.2-i2v"}}
    assert vocab["mode"] == {"text-to-image", "frames-start", "image-edit"}
    assert vocab["kind"] == {"scene-master", "empty-plate", "start-frame"}
    assert vocab["reusable"] == {"general", "style-family", "scene-specific"}
    assert vocab["codes"] == {"subject_scale_mismatch", "mouth_open", "push_accelerates",
                              "prompt_not_persisted"}


def test_vocab_stage_line_only_not_later_prose(vocab):
    assert vocab["stage"] == {"reference", "still", "video", "post"}


def test_vocab_pattern_status_survives_fenced_headings(vocab):
    assert vocab["pattern_status"] == {"proven", "tentative", "deprecated"}


def test_fenced_heading_does_not_end_section():
    body = lib._sections(SCHEMA)["6"]
    assert "`status: proven`" in body          # prose AFTER the fence stays in §6


def test_vocab_backticked_table_header_is_not_a_platform(vocab):
    assert "platform" not in vocab["platform"]


def test_vocab_parenthetical_field_name_is_not_a_status(vocab):
    assert vocab["status"] == {"approved", "candidate", "rejected", "superseded"}


def test_vocab_missing_section_raises_instead_of_loading_empty():
    with pytest.raises(lib.VocabError):
        lib.parse_vocab(SCHEMA.replace("## 3. 結論詞表", "## 結論詞表"), CODES)


def test_vocab_empty_family_raises():
    with pytest.raises(lib.VocabError):
        lib.parse_vocab(SCHEMA, "# no tables here\n")


def _real_library():
    """The live library dir, or None — never raising.

    Loaded BY PATH through `_load`, like every other scripts/ module here,
    rather than `from vault_paths import ...`. A bare import trusts
    `sys.modules`, and a sibling test module installs a `vault_paths` stub
    there; this runs inside a `skipif` argument, i.e. at COLLECTION, so the
    ImportError aborted the entire session rather than failing one test.

    The except is deliberate and narrow in effect: this helper answers "is
    there a real vault to test against", and every way of answering "no"
    should skip, not error.
    """
    try:
        root = _load("vault_paths").vault_root()
    except Exception:
        return None
    return root / lib.LIBRARY_REL if root else None


@pytest.mark.skipif(not (_real_library() and (_real_library() / "SCHEMA.md").exists()),
                    reason="no vault / library configured")
def test_real_library_notes_parse():
    v = lib.load_vocab(_real_library())
    assert "veo-3.1-fast" in v["platform"]["google-flow"]
    assert "prompt_not_persisted" in v["codes"]
    assert "platform" not in v["platform"] and "status_reason" not in v["status"]
    assert v["pattern_status"] == {"proven", "tentative", "deprecated"}


# ── attempt validation ───────────────────────────────────────────────────────

def test_valid_attempt_is_clean(env, vocab):
    assert lib.validate_attempt(attempt(), vocab, env[0], patterns=set()) == []


@pytest.mark.parametrize("field", ["attempt_id", "project", "project_path", "stage",
                                   "platform", "model", "mode", "verdict"])
def test_required_field(env, vocab, field):
    row = attempt()
    del row[field]
    assert "missing_field" in codes_of(lib.validate_attempt(row, vocab, env[0]))


def test_prompt_key_required(env, vocab):
    row = attempt()
    del row["prompt"]
    assert "missing_field" in codes_of(lib.validate_attempt(row, vocab, env[0]))


@pytest.mark.parametrize("field,value", [
    ("verdict", "ok"), ("stage", "shoot"), ("platform", "runway"), ("mode", "t2v"),
])
def test_vocab_field_rejected(env, vocab, field, value):
    # Assert on the message, not just the code: an unknown platform also makes
    # the model-under-platform check fire, so `"bad_vocab" in codes` stays green
    # with the platform check deleted outright (found by mutation).
    found = lib.validate_attempt(attempt(**{field: value}), vocab, env[0])
    assert any(c == "bad_vocab" and m.startswith(f"{field} {value!r}") for c, m in found)


@pytest.mark.parametrize("field", ["verdict", "stage", "platform", "mode"])
def test_non_string_vocab_value_is_a_finding_not_a_crash(env, vocab, field):
    found = lib.validate_attempt(attempt(**{field: ["pass"]}), vocab, env[0])
    assert "bad_vocab" in codes_of(found)


def test_non_string_code_is_a_finding_not_a_crash(env, vocab):
    assert "bad_vocab" in codes_of(lib.validate_attempt(attempt(codes=[["mouth_open"]]), vocab, env[0]))


def test_model_must_belong_to_platform(env, vocab):
    row = attempt(platform="comfyui", model="veo-3.1-fast")
    assert "bad_vocab" in codes_of(lib.validate_attempt(row, vocab, env[0]))


def test_unknown_failure_code(env, vocab):
    row = attempt(codes=["mouth_open", "looks_weird"])
    assert "bad_vocab" in codes_of(lib.validate_attempt(row, vocab, env[0]))


@pytest.mark.parametrize("over", [
    {"inputs": [{"role": "start", "sha256": "0d1bbb35", "path": "proj/in.jpeg"}]},
    {"output": {"sha256": H64.upper(), "path": "proj/out.mp4"}},
    {"prompt_sha256": "zz"},
])
def test_hash_format(env, vocab, over):
    assert "bad_hash" in codes_of(lib.validate_attempt(attempt(**over), vocab, env[0]))


def test_prompt_hash_must_match_prompt(env, vocab):
    row = attempt(prompt_sha256=lib.sha256_text("a different prompt"))
    assert "bad_hash" in codes_of(lib.validate_attempt(row, vocab, env[0]))
    ok = attempt(prompt_sha256=lib.sha256_text("slow push toward her"))
    assert lib.validate_attempt(ok, vocab, env[0]) == []


@pytest.mark.parametrize("path", ["C:/x/out.mp4", "/proj/out.mp4", "proj\\out.mp4",
                                  "../outside.txt", "proj/../../outside.txt"])
def test_path_must_be_vault_relative(env, vocab, path):
    row = attempt(output={"sha256": H64B, "path": path})
    assert "bad_path" in codes_of(lib.validate_attempt(row, vocab, env[0]))


@pytest.mark.parametrize("over", [
    {"project_path": "nope"},
    {"inputs": [{"role": "start", "sha256": H64, "path": "proj/gone.jpeg"}]},
    {"output": {"sha256": H64B, "path": "proj/gone.mp4"}},
    {"evidence": {"path": "proj/gone.md"}},
    {"source": {"file": "proj/gone.jsonl"}},
])
def test_path_must_resolve(env, vocab, over):
    assert "path_missing" in codes_of(lib.validate_attempt(attempt(**over), vocab, env[0]))


def test_prompt_null_needs_not_persisted_code(env, vocab):
    row = attempt(prompt=None, verdict="unreviewed", codes=[])
    assert "prompt_missing" in codes_of(lib.validate_attempt(row, vocab, env[0]))
    row["codes"] = ["prompt_not_persisted"]
    assert lib.validate_attempt(row, vocab, env[0]) == []


def test_prompt_blank_rejected(env, vocab):
    assert "prompt_missing" in codes_of(lib.validate_attempt(attempt(prompt="  "), vocab, env[0]))


@pytest.mark.parametrize("verdict", ["fail", "partial"])
def test_fail_and_partial_need_codes(env, vocab, verdict):
    row = attempt(verdict=verdict, codes=[])
    assert "needs_codes" in codes_of(lib.validate_attempt(row, vocab, env[0]))


@pytest.mark.parametrize("verdict", ["fail", "partial"])
def test_fail_and_partial_need_reason(env, vocab, verdict):
    row = attempt(verdict=verdict, reason="")
    assert "needs_reason" in codes_of(lib.validate_attempt(row, vocab, env[0]))


def test_pass_needs_no_codes(env, vocab):
    assert lib.validate_attempt(attempt(verdict="pass", codes=[], reason=None), vocab, env[0]) == []


REVIEWED = ["pass", "partial", "fail", "rejected-human", "superseded"]


@pytest.mark.parametrize("verdict", REVIEWED)
def test_reviewed_verdict_needs_output(env, vocab, verdict):
    row = attempt(verdict=verdict, output=None)
    assert "needs_output" in codes_of(lib.validate_attempt(row, vocab, env[0]))


@pytest.mark.parametrize("verdict", REVIEWED)
def test_reviewed_verdict_needs_evidence(env, vocab, verdict):
    row = attempt(verdict=verdict, evidence=None)
    assert "needs_evidence" in codes_of(lib.validate_attempt(row, vocab, env[0]))


@pytest.mark.parametrize("output", [{"sha256": None, "path": None}, "whatever"])
def test_reviewed_output_must_point_at_something(env, vocab, output):
    row = attempt(verdict="pass", codes=[], output=output)
    assert "needs_output" in codes_of(lib.validate_attempt(row, vocab, env[0]))


def test_unreviewed_needs_neither_output_nor_evidence(env, vocab):
    row = attempt(verdict="unreviewed", codes=[], output=None, evidence=None)
    assert lib.validate_attempt(row, vocab, env[0]) == []


def test_generation_failed_has_no_output(env, vocab):
    row = attempt(verdict="generation-failed", codes=[])
    assert "needs_output" in codes_of(lib.validate_attempt(row, vocab, env[0]))


def test_null_input_hash_needs_a_reason_or_note(env, vocab):
    inputs = [{"role": "start", "sha256": None, "path": None}]
    row = attempt(verdict="unreviewed", codes=[], reason=None, inputs=inputs)
    assert "needs_reason" in codes_of(lib.validate_attempt(row, vocab, env[0]))
    inputs[0]["note"] = "Flow asset name only; file never downloaded"
    assert lib.validate_attempt(row, vocab, env[0]) == []


def test_backfill_needs_source(env, vocab):
    row = attempt(source=None)
    assert "missing_field" in codes_of(lib.validate_attempt(row, vocab, env[0]))


@pytest.mark.parametrize("credits", [{"quoted": 0, "charged": "unknown"}, "ten"])
def test_credits_must_be_numbers_or_null(env, vocab, credits):
    assert "bad_credits" in codes_of(lib.validate_attempt(attempt(credits=credits), vocab, env[0]))


def test_credits_null_value_ok(env, vocab):
    assert lib.validate_attempt(attempt(credits={"quoted": 0, "charged": None}), vocab, env[0]) == []


def test_unknown_pattern(env, vocab):
    row = attempt(pattern_ids=["closed-lip"])
    assert "unknown_pattern" in codes_of(lib.validate_attempt(row, vocab, env[0], patterns=set()))
    assert lib.validate_attempt(row, vocab, env[0], patterns={"closed-lip"}) == []


def test_lyrics_field_rejected_even_nested(env, vocab):
    row = attempt(settings={"scene": {"lyrics": "…"}})
    assert "lyrics_stored" in codes_of(lib.validate_attempt(row, vocab, env[0]))


def test_non_object_row(env, vocab):
    assert codes_of(lib.validate_attempt([1, 2], vocab, env[0])) == ["not_object"]


# ── asset validation ─────────────────────────────────────────────────────────

def test_valid_asset_is_clean(env, vocab):
    assert lib.validate_asset(asset(), vocab, env[0], attempt_ids=set(), asset_ids={"P:A1"}) == []


@pytest.mark.parametrize("field,value", [("kind", "poster"), ("status", "ok"), ("reusable", "maybe"),
                                         ("kind", ["scene-master"])])
def test_asset_vocab(env, vocab, field, value):
    assert "bad_vocab" in codes_of(lib.validate_asset(asset(**{field: value}), vocab, env[0]))


def test_asset_model_must_belong_to_platform(env, vocab):
    row = asset(origin={"platform": "comfyui", "model": "nano-banana-pro"})
    assert "bad_vocab" in codes_of(lib.validate_asset(row, vocab, env[0]))


@pytest.mark.parametrize("field", ["asset_id", "kind", "path", "status", "status_reason",
                                   "origin", "rights", "sha256"])
def test_asset_required(env, vocab, field):
    row = asset()
    del row[field]
    assert "missing_field" in codes_of(lib.validate_asset(row, vocab, env[0]))


def test_asset_dangling_refs(env, vocab):
    row = asset(parents=["P:ghost"], origin={"platform": "google-flow", "attempt_id": "P:x:1:1"})
    found = codes_of(lib.validate_asset(row, vocab, env[0], attempt_ids=set(), asset_ids={"P:A1"}))
    assert found.count("dangling_ref") == 2


def test_asset_lyrics_rejected(env, vocab):
    assert "lyrics_stored" in codes_of(lib.validate_asset(asset(lyrics="…"), vocab, env[0]))


# ── record / upsert ──────────────────────────────────────────────────────────

def _rows(library: Path, name=lib.ATTEMPTS) -> list[dict]:
    return lib.read_jsonl(library / name)


def test_record_writes_and_is_idempotent(env):
    vault, library = env
    r1 = lib.record(library, vault, [attempt()], kind="attempt", now="2026-09-16T10:00:00+10:00")
    assert r1["ok"] and r1["added"] == 1
    r2 = lib.record(library, vault, [attempt()], kind="attempt", now="2026-09-17T10:00:00+10:00")
    assert r2["ok"] and r2["added"] == 0 and r2["unchanged"] == 1 and not r2["written"]
    rows = _rows(library)
    assert len(rows) == 1 and rows[0]["recorded_at"] == "2026-09-16T10:00:00+10:00"


def test_record_conflict_without_update_writes_nothing(env):
    vault, library = env
    lib.record(library, vault, [attempt()], kind="attempt")
    res = lib.record(library, vault, [attempt(reason="changed")], kind="attempt")
    assert not res["ok"] and res["conflicts"]
    assert _rows(library)[0]["reason"] == "0-1.7 s usable"


SUBMITTED = dict(verdict="unreviewed", codes=[], reason=None, evidence=None)
REVIEW = {"attempt_id": "P:video:S1:1", "verdict": "fail", "codes": ["push_accelerates"],
          "reason": "push speeds up", "evidence": {"path": "proj/review.md"}}


def test_record_update_merges_review_fields(env):
    vault, library = env
    lib.record(library, vault, [attempt(**SUBMITTED)], kind="attempt")
    res = lib.record(library, vault, [REVIEW], kind="attempt", update=True)
    assert res["ok"] and res["updated"] == 1
    row = _rows(library)[0]
    assert row["verdict"] == "fail" and row["prompt"] == "slow push toward her"


@pytest.mark.parametrize("update", [True, False])
def test_rerunning_backfill_never_undoes_a_review(env, update):
    vault, library = env
    lib.record(library, vault, [attempt(**SUBMITTED)], kind="attempt")
    lib.record(library, vault, [REVIEW], kind="attempt", update=True)
    rerun = [attempt(**SUBMITTED), attempt(attempt_id="P:video:S2:1", shot="S2", **SUBMITTED)]
    res = lib.record(library, vault, rerun, kind="attempt", update=update)
    assert res["ok"] and res["added"] == 1 and not res["conflicts"]
    first = _rows(library)[0]
    assert first["verdict"] == "fail" and first["codes"] == ["push_accelerates"]


def test_update_on_an_unreviewed_row_still_replaces_its_fields(env):
    # Review preservation applies only when the STORED row carries a real
    # verdict; an unreviewed row re-recorded with a new reason must take it.
    vault, library = env
    lib.record(library, vault, [attempt(**{**SUBMITTED, "reason": "first note"})], kind="attempt")
    res = lib.record(library, vault, [attempt(**{**SUBMITTED, "reason": "second note"})],
                     kind="attempt", update=True)
    assert res["ok"] and res["updated"] == 1
    assert _rows(library)[0]["reason"] == "second note"


def test_explicit_new_verdict_replaces_a_review(env):
    vault, library = env
    lib.record(library, vault, [attempt(**SUBMITTED)], kind="attempt")
    lib.record(library, vault, [REVIEW], kind="attempt", update=True)
    res = lib.record(library, vault, [{"attempt_id": "P:video:S1:1", "verdict": "pass", "codes": [],
                                       "reason": None}], kind="attempt", update=True)
    assert res["ok"] and _rows(library)[0]["verdict"] == "pass"


def test_update_that_breaks_the_row_writes_nothing(env):
    vault, library = env
    lib.record(library, vault, [attempt(**SUBMITTED)], kind="attempt")
    res = lib.record(library, vault, [{"attempt_id": "P:video:S1:1", "verdict": "fail", "codes": []}],
                     kind="attempt", update=True)
    assert not res["ok"] and "needs_codes" in [e["code"] for e in res["errors"]]
    assert _rows(library)[0]["verdict"] == "unreviewed"


def test_batch_is_all_or_nothing(env):
    vault, library = env
    good = attempt()
    bad = attempt(attempt_id="P:video:S2:1", shot="S2", verdict="nope")
    res = lib.record(library, vault, [good, bad], kind="attempt")
    assert not res["ok"]
    assert not (library / lib.ATTEMPTS).exists()


def test_batch_duplicate_ids_conflict(env):
    vault, library = env
    res = lib.record(library, vault, [attempt(), attempt()], kind="attempt")
    assert not res["ok"] and res["conflicts"]


def test_non_object_input_row_writes_nothing(env):
    vault, library = env
    res = lib.record(library, vault, [attempt(), "oops"], kind="attempt")
    assert not res["ok"] and not (library / lib.ATTEMPTS).exists()


def test_attempt_id_derived_and_prompt_hashed(env):
    vault, library = env
    row = attempt()
    del row["attempt_id"]
    assert lib.record(library, vault, [row], kind="attempt")["ok"]
    stored = _rows(library)[0]
    assert stored["attempt_id"] == "P:video:S1:1"
    assert stored["prompt_sha256"] == lib.sha256_text("slow push toward her")


def test_record_asset_checks_parents_within_batch(env):
    vault, library = env
    child = asset(asset_id="P:A2", parents=["P:A1"])
    assert lib.record(library, vault, [asset(), child], kind="asset")["ok"]
    orphan = asset(asset_id="P:A3", parents=["P:missing"])
    assert not lib.record(library, vault, [orphan], kind="asset")["ok"]


def test_concurrent_records_keep_every_row(env):
    vault, library = env
    results = []

    def one(i):
        row = attempt(attempt_id=f"P:video:S{i}:1", shot=f"S{i}")
        results.append(lib.record(library, vault, [row], kind="attempt"))

    threads = [threading.Thread(target=one, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(r["ok"] for r in results)
    assert len(_rows(library)) == 12
    assert not lib.lock_path(library / lib.ATTEMPTS).exists()
    assert not list(library.glob("*.lock")) and not list(library.glob(".*.lock"))


def test_lock_times_out_when_held(env):
    _, library = env
    target = library / lib.ATTEMPTS
    held = lib.lock_path(target)
    held.parent.mkdir(parents=True, exist_ok=True)
    held.write_text("someone-else", encoding="utf-8")
    try:
        with pytest.raises(TimeoutError):
            with lib.file_lock(target, timeout=0.3):
                pass
        assert held.read_text(encoding="utf-8") == "someone-else"
    finally:
        held.unlink(missing_ok=True)


def test_cli_record_and_json_envelope(env, capsys):
    vault, library = env
    argv = ["--vault", str(vault), "--library", str(library), "--json",
            "record-attempt", "--data", json.dumps(attempt())]
    assert lib.main(argv) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["data"]["added"] == 1
    bad = ["--vault", str(vault), "--library", str(library), "--json",
           "record-attempt", "--data", json.dumps(attempt(attempt_id="X:video:S9:1", verdict="?"))]
    assert lib.main(bad) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "invalid_rows"
    human = [a for a in bad if a != "--json"]                 # human mode owns its own exit code
    assert lib.main(human) == 1
    assert "NOTHING WRITTEN" in capsys.readouterr().out


def test_cli_io_error_is_an_envelope(env, capsys):
    vault, library = env
    held = lib.lock_path(library / lib.ATTEMPTS)
    held.parent.mkdir(parents=True, exist_ok=True)
    held.write_text("someone-else", encoding="utf-8")
    lib.LOCK_TIMEOUT_S, saved = 0.2, lib.LOCK_TIMEOUT_S
    try:
        rc = lib.main(["--vault", str(vault), "--library", str(library), "--json",
                       "record-attempt", "--data", json.dumps(attempt())])
    finally:
        lib.LOCK_TIMEOUT_S = saved
        held.unlink(missing_ok=True)
    assert rc == 1 and json.loads(capsys.readouterr().out)["code"] == "io_error"


def test_parse_rows_accepts_object_array_and_jsonl():
    a, b = {"attempt_id": "a"}, {"attempt_id": "b"}
    assert lib.parse_rows(json.dumps(a)) == [a]
    assert lib.parse_rows(json.dumps([a, b])) == [a, b]
    assert lib.parse_rows(json.dumps(a) + "\n" + json.dumps(b)) == [a, b]
    with pytest.raises(lib.RowError):
        lib.parse_rows('{"attempt_id": "a"}\n{broken')


# ── stats + patterns ─────────────────────────────────────────────────────────

def test_stats_counts_codes_and_verdicts():
    rows = [attempt(), attempt(attempt_id="x", verdict="fail", codes=["mouth_open", "push_accelerates"]),
            "not a row"]
    st = lib.stats(rows)
    assert st["total"] == 2
    assert st["verdict"] == {"partial": 1, "fail": 1}
    assert st["code"] == {"mouth_open": 2, "push_accelerates": 1}


PATTERN = """---
type: mv-prompt-pattern
pattern_id: slow-push
status: tentative
evidence_refreshed: 2026-01-01
evidence: {pass: 0, partial: 0, fail: 0}
---

# 慢推

```markdown
<!-- mv-library:evidence -->
- quoted example, leave alone
<!-- /mv-library:evidence -->
```

## 證據
<!-- mv-library:evidence -->
- old
<!-- /mv-library:evidence -->
"""


def _pattern_attempts():
    base = dict(pattern_ids=["slow-push"])
    return [
        attempt(attempt_id="A:1", project="A", verdict="pass", **base),
        attempt(attempt_id="A:2", project="A", verdict="pass", **base),
        attempt(attempt_id="B:1", project="B", verdict="fail", **base),
        attempt(attempt_id="C:1", project="C", verdict="partial", **base),
        attempt(attempt_id="D:1", project="D", verdict="pass"),          # not tagged
    ]


def test_pattern_evidence_counts_and_projects():
    ev = lib.pattern_evidence(_pattern_attempts(), "slow-push")
    assert (ev["pass"], ev["partial"], ev["fail"], ev["pass_projects"]) == (2, 1, 1, 1)


def test_pattern_evidence_tolerates_null_verdict_and_id():
    rows = _pattern_attempts() + [attempt(attempt_id=None, verdict=None, pattern_ids=["slow-push"]), 7]
    text = lib.refresh_pattern_text(PATTERN, rows, "2026-09-16")
    assert "- None: `None`" in text


def test_refresh_rewrites_counts_block_and_date():
    new = lib.refresh_pattern_text(PATTERN, _pattern_attempts(), "2026-09-16")
    assert "evidence: {pass: 2, partial: 1, fail: 1, pass_projects: 1}" in new
    assert "evidence_refreshed: 2026-09-16" in new
    assert "- pass: `A:1`, `A:2`" in new and "- old" not in new


def test_refresh_leaves_fenced_marker_example_alone():
    new = lib.refresh_pattern_text(PATTERN, _pattern_attempts(), "2026-09-16")
    assert "- quoted example, leave alone" in new


def test_refresh_is_stable_when_nothing_moved():
    once = lib.refresh_pattern_text(PATTERN, _pattern_attempts(), "2026-09-16")
    assert lib.refresh_pattern_text(once, _pattern_attempts(), "2030-01-01") == once


def test_refresh_handles_crlf_notes_and_keeps_crlf():
    crlf = PATTERN.replace("\n", "\r\n")
    new = lib.refresh_pattern_text(crlf, _pattern_attempts(), "2026-09-16")
    assert "evidence: {pass: 2, partial: 1, fail: 1, pass_projects: 1}\r\n" in new
    assert "\n" not in new.replace("\r\n", "")


def test_refresh_replaces_block_style_evidence():
    block = PATTERN.replace("evidence: {pass: 0, partial: 0, fail: 0}",
                            "evidence:\n  pass: 0\n  fail: 0")
    new = lib.refresh_pattern_text(block, _pattern_attempts(), "2026-09-16")
    fm = lib._frontmatter(new)[0]
    assert "evidence: {pass: 2, partial: 1, fail: 1, pass_projects: 1}" in fm
    assert "  pass: 0" not in fm


def test_refresh_patterns_writes_files(env):
    vault, library = env
    (library / "patterns").mkdir()
    (library / "patterns" / "slow-push.md").write_text(PATTERN, encoding="utf-8")
    lib.write_jsonl_atomic(library / lib.ATTEMPTS, _pattern_attempts())
    assert lib.refresh_patterns(library, today="2026-09-16", dry_run=True) == ["slow-push.md"]
    assert "pass: 0" in (library / "patterns" / "slow-push.md").read_text(encoding="utf-8")
    assert lib.refresh_patterns(library, today="2026-09-16") == ["slow-push.md"]
    assert lib.refresh_patterns(library, today="2026-09-17") == []


def test_pattern_ids_reads_crlf_notes(env):
    _, library = env
    (library / "patterns").mkdir()
    (library / "patterns" / "slow-push.md").write_bytes(PATTERN.replace("\n", "\r\n").encode("utf-8"))
    assert lib.pattern_ids(library) == {"slow-push"}


# ── checker ──────────────────────────────────────────────────────────────────

def _scan_codes(library, vault):
    return [f["code"] for f in chk.scan(library, vault)["findings"]]


def test_checker_clean_library(env):
    vault, library = env
    lib.write_jsonl_atomic(library / lib.ATTEMPTS, [attempt()])
    lib.write_jsonl_atomic(library / lib.ASSETS, [asset()])
    assert chk.scan(library, vault)["findings"] == []


def test_checker_flags_row_rules(env):
    vault, library = env
    lib.write_jsonl_atomic(library / lib.ATTEMPTS, [attempt(verdict="fail", codes=[])])
    assert "needs_codes" in _scan_codes(library, vault)


def test_checker_duplicate_ids(env):
    vault, library = env
    lib.write_jsonl_atomic(library / lib.ATTEMPTS, [attempt(), attempt()])
    lib.write_jsonl_atomic(library / lib.ASSETS, [asset(), asset()])
    assert _scan_codes(library, vault).count("duplicate_id") == 2


def test_checker_bad_json_line(env):
    vault, library = env
    (library / lib.ATTEMPTS).write_text(json.dumps(attempt()) + "\n{nope\n", encoding="utf-8")
    assert "bad_json" in _scan_codes(library, vault)


def test_checker_non_utf8_file_is_a_finding(env):
    vault, library = env
    (library / lib.ATTEMPTS).write_bytes(b'{"attempt_id": "\xff"}\n')
    assert "bad_json" in _scan_codes(library, vault)


def test_checker_reports_malformed_rows_without_crashing(env):
    vault, library = env
    (library / "patterns").mkdir()
    (library / "patterns" / "slow-push.md").write_text(PATTERN, encoding="utf-8")
    rows = [attempt(verdict=None, pattern_ids=["slow-push"]), [1, 2], "x",
            attempt(attempt_id="P:video:S3:1", verdict=["pass"])]
    lib.write_jsonl_atomic(library / lib.ATTEMPTS, rows)
    codes = _scan_codes(library, vault)
    assert "not_object" in codes and "missing_field" in codes and "bad_vocab" in codes


def test_checker_asset_dangling_parent(env):
    vault, library = env
    lib.write_jsonl_atomic(library / lib.ASSETS, [asset(parents=["P:ghost"])])
    assert "dangling_ref" in _scan_codes(library, vault)


def test_checker_unproven_and_stale_pattern(env):
    vault, library = env
    (library / "patterns").mkdir()
    proven = PATTERN.replace("status: tentative", "status: proven")
    (library / "patterns" / "slow-push.md").write_text(proven, encoding="utf-8")
    lib.write_jsonl_atomic(library / lib.ATTEMPTS, _pattern_attempts())
    codes = _scan_codes(library, vault)
    assert "unproven" in codes and "stale_evidence" in codes


def test_checker_proven_with_two_projects_is_not_unproven(env):
    vault, library = env
    (library / "patterns").mkdir()
    rows = _pattern_attempts() + [attempt(attempt_id="E:1", project="E", verdict="pass",
                                          pattern_ids=["slow-push"])]
    text = lib.refresh_pattern_text(PATTERN.replace("status: tentative", "status: proven"), rows, "2026-09-16")
    (library / "patterns" / "slow-push.md").write_text(text, encoding="utf-8")
    lib.write_jsonl_atomic(library / lib.ATTEMPTS, rows)
    assert _scan_codes(library, vault) == []


def test_checker_bad_pattern_status(env):
    vault, library = env
    (library / "patterns").mkdir()
    (library / "patterns" / "slow-push.md").write_text(
        PATTERN.replace("status: tentative", "status: great"), encoding="utf-8")
    assert "bad_vocab" in _scan_codes(library, vault)


def test_checker_main_exit_code_and_skip(env, capsys):
    vault, library = env
    lib.write_jsonl_atomic(library / lib.ATTEMPTS, [
        attempt(verdict="fail", codes=[]),
        attempt(attempt_id="P:video:S2:1", stage="nope"),
    ])
    assert chk.main(["--vault", str(vault), "--library", str(library)]) == 1
    capsys.readouterr()
    assert chk.main(["--vault", str(vault), "--library", str(vault / "absent"), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_checker_vocab_failure_is_loud(env, capsys):
    vault, library = env
    (library / "failure-codes.md").write_text("# empty\n", encoding="utf-8")
    assert chk.main(["--vault", str(vault), "--library", str(library), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "vocab"


# ── second hostile-review pins ───────────────────────────────────────────────

def test_line_separator_in_prompt_round_trips(env):
    vault, library = env
    assert lib.record(library, vault, [attempt(prompt="line one\u2028line two\x85end")], kind="attempt")["ok"]
    second = attempt(attempt_id="P:video:S2:1", shot="S2")
    assert lib.record(library, vault, [second], kind="attempt")["ok"]
    rows = _rows(library)
    assert len(rows) == 2 and rows[0]["prompt"] == "line one\u2028line two\x85end"
    assert chk.scan(library, vault)["findings"] == []


@pytest.mark.parametrize("field,value", [("pattern_ids", 5), ("codes", 5), ("inputs", {}),
                                         ("used_in_cut", "v10"), ("pattern_ids", "slow-push")])
def test_non_list_fields_are_findings_not_crashes(env, vocab, field, value):
    found = lib.validate_attempt(attempt(**{field: value}), vocab, env[0], patterns={"slow-push"})
    assert "not_list" in codes_of(found)


def test_asset_non_list_parents_is_a_finding(env, vocab):
    found = lib.validate_asset(asset(parents=5), vocab, env[0], asset_ids={"P:A1"})
    assert "not_list" in codes_of(found)


def test_string_pattern_ids_never_counts_as_evidence():
    rows = [attempt(attempt_id="A:1", verdict="pass", pattern_ids="slow-push-x")]
    assert lib.pattern_evidence(rows, "slow-push")["pass"] == 0


def test_stats_tolerates_non_list_codes():
    assert lib.stats([attempt(codes=5)])["code"] == {}


def test_list_valued_id_is_a_conflict_not_a_crash(env):
    vault, library = env
    res = lib.record(library, vault, [attempt(attempt_id=["x"])], kind="attempt")
    assert not res["ok"] and res["conflicts"]


def test_stored_list_id_does_not_crash_asset_recording(env):
    vault, library = env
    lib.write_jsonl_atomic(library / lib.ATTEMPTS, [attempt(attempt_id=["bad"])])
    assert lib.record(library, vault, [asset()], kind="asset")["ok"]


def test_checker_non_utf8_pattern_note_is_a_finding(env):
    vault, library = env
    (library / "patterns").mkdir()
    (library / "patterns" / "broken.md").write_bytes(b"---\npattern_id: x\xff\n---\n")
    assert "bad_json" in _scan_codes(library, vault)


def test_checker_crash_is_a_loud_envelope(env, capsys, monkeypatch):
    vault, library = env

    def boom(*a, **k):
        raise TypeError("boom")

    monkeypatch.setattr(chk, "scan", boom)
    assert chk.main(["--vault", str(vault), "--library", str(library), "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["code"] == "crash" and "boom" in out["message"]


@pytest.mark.parametrize("update", [True, False])
def test_rerun_keeps_reviewer_edited_output_and_cut(env, update):
    vault, library = env
    (vault / "proj" / "out2.mp4").write_text("x", encoding="utf-8")
    lib.record(library, vault, [attempt(**{**SUBMITTED, "output": None})], kind="attempt")
    review = {**REVIEW, "output": {"sha256": None, "path": "proj/out2.mp4"},
              "used_in_cut": [{"version": "v10"}]}
    assert lib.record(library, vault, [review], kind="attempt", update=True)["ok"]
    rerun = lib.record(library, vault, [attempt(**{**SUBMITTED, "output": None})], kind="attempt", update=update)
    assert rerun["ok"] and not rerun["conflicts"] and rerun["updated"] == 0
    row = _rows(library)[0]
    assert row["output"]["path"] == "proj/out2.mp4" and row["used_in_cut"] == [{"version": "v10"}]


def test_rerun_fills_fields_the_reviewed_row_lacks_only_with_update(env):
    vault, library = env
    lib.record(library, vault, [attempt(**SUBMITTED)], kind="attempt")
    lib.record(library, vault, [REVIEW], kind="attempt", update=True)
    extra = attempt(**SUBMITTED, batch_id="batch-12")
    assert lib.record(library, vault, [extra], kind="attempt")["unchanged"] == 1
    res = lib.record(library, vault, [extra], kind="attempt", update=True)
    assert res["updated"] == 1
    row = _rows(library)[0]
    assert row["batch_id"] == "batch-12" and row["verdict"] == "fail"


def test_reset_review_puts_a_row_back_to_unreviewed(env):
    vault, library = env
    lib.record(library, vault, [attempt(**SUBMITTED)], kind="attempt")
    lib.record(library, vault, [REVIEW], kind="attempt", update=True)
    back = {"attempt_id": "P:video:S1:1", "verdict": "unreviewed", "codes": [], "reason": None, "evidence": None}
    assert lib.record(library, vault, [back], kind="attempt", update=True)["unchanged"] == 1
    res = lib.record(library, vault, [back], kind="attempt", update=True, reset_review=True)
    assert res["ok"] and res["updated"] == 1 and _rows(library)[0]["verdict"] == "unreviewed"


def test_lock_lives_outside_the_library(env):
    _, library = env
    assert library not in lib.lock_path(library / lib.ATTEMPTS).parents


def test_release_never_deletes_another_writers_lock(env):
    _, library = env
    target = library / lib.ATTEMPTS
    with lib.file_lock(target):
        lib.lock_path(target).write_text("another-writer", encoding="utf-8")   # replaced mid-hold
    assert lib.lock_path(target).read_text(encoding="utf-8") == "another-writer"
    lib.lock_path(target).unlink()


def test_stale_lock_is_broken(env):
    _, library = env
    target = library / lib.ATTEMPTS
    held = lib.lock_path(target)
    held.parent.mkdir(parents=True, exist_ok=True)
    held.write_text("crashed-writer", encoding="utf-8")
    old = time.time() - lib.LOCK_STALE_S - 5
    os.utime(held, (old, old))
    with lib.file_lock(target, timeout=1.0):
        assert held.read_text(encoding="utf-8") != "crashed-writer"
    assert not held.exists()


def test_bom_pattern_note_is_read(env):
    _, library = env
    (library / "patterns").mkdir()
    (library / "patterns" / "slow-push.md").write_bytes(("\ufeff" + PATTERN).encode("utf-8"))
    assert lib.pattern_ids(library) == {"slow-push"}
    new = lib.refresh_pattern_text("\ufeff" + PATTERN, _pattern_attempts(), "2026-09-16")
    assert new.startswith("\ufeff---\n") and "pass: 2" in new


def test_refresh_replaces_block_list_evidence():
    block = PATTERN.replace("evidence: {pass: 0, partial: 0, fail: 0}", "evidence:\n- a\n- b")
    fm = lib._frontmatter(lib.refresh_pattern_text(block, _pattern_attempts(), "2026-09-16"))[0]
    assert "- a" not in fm and "evidence: {pass: 2" in fm


def test_refresh_keeps_trailing_comment():
    commented = PATTERN.replace("evidence: {pass: 0, partial: 0, fail: 0}",
                                "evidence: {pass: 0, partial: 0, fail: 0}   # 由腳本更新")
    new = lib.refresh_pattern_text(commented, _pattern_attempts(), "2026-09-16")
    assert "evidence: {pass: 2, partial: 1, fail: 1, pass_projects: 1}   # 由腳本更新" in new


def test_refresh_mixed_line_endings_normalise_to_lf():
    mixed = PATTERN.replace("\n", "\r\n", 3)
    assert "\r" not in lib.refresh_pattern_text(mixed, _pattern_attempts(), "2026-09-16")


def test_rejected_human_needs_reason(env, vocab):
    row = attempt(verdict="rejected-human", codes=[], reason=None)
    assert "needs_reason" in codes_of(lib.validate_attempt(row, vocab, env[0]))


def test_null_input_hash_explained_by_row_reason(env, vocab):
    inputs = [{"role": "start", "sha256": None, "path": None}]
    row = attempt(verdict="unreviewed", codes=[], reason="Flow asset name only", inputs=inputs)
    assert lib.validate_attempt(row, vocab, env[0]) == []


def test_boolean_credits_rejected(env, vocab):
    assert "bad_credits" in codes_of(lib.validate_attempt(attempt(credits={"quoted": True}), vocab, env[0]))
