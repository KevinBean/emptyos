"""WriteDesk build — assemble assets from the EmptyOS repo, freeze with PyInstaller.

The exe reuses the REAL app pages + the jianpu renderer from
``apps/public/standard/`` — copied at build time with a few mechanical patches
(PDF buttons → print views, compose button removed). There is no forked UI
source: re-running this build picks up upstream page changes automatically.
Every patch asserts its marker string so an upstream edit that breaks a patch
fails the build loudly instead of shipping a broken page.

Usage (from repo root or this dir):
    python products/writedesk/build.py            # assets + onefile exe
    python products/writedesk/build.py --assets   # assets only (dev: run launcher.py)
    python products/writedesk/build.py --console  # keep a console window (debug build)

Output: products/writedesk/dist/WriteDesk/ — WriteDesk.exe + config.toml + 使用说明.txt
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
REPO = HERE.parent.parent
ASSETS = HERE / "assets"

STATIC_FILES = ["theme.css", "eos.js", "eos-components.css", "eos-components.js",
                "eos-i18n.js", "eos-keys.css", "eos-keys.js", "eos-tour.js"]
WE_PAGE = REPO / "apps/public/standard/writing-editor/pages/index.html"
JP_DIR = REPO / "apps/public/standard/jianpu/pages"
WE_APP = REPO / "apps/public/standard/writing-editor/app.py"


def patch(text: str, old: str, new: str, what: str) -> str:
    assert old in text, f"patch marker missing ({what}) — upstream page changed, update build.py"
    return text.replace(old, new, 1)


def verify_shared_helpers():
    """Drift guard: server.py keeps local copies of small pure helpers
    (safe_filename, word_count, parse_llm_json) because the frozen exe must
    not import the emptyos package. The BUILD machine has emptyos installed,
    so verify the copies still behave like their SDK/app originals on test
    vectors — semantic divergence fails the build loudly instead of shipping.
    """
    import importlib.util
    import tempfile

    spec = importlib.util.spec_from_file_location("wd_server", HERE / "server.py")
    srv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(srv)

    # 1. safe_filename ≍ emptyos.sdk.utils.safe_note_filename (pure SDK module —
    #    no kernel boot; see .claude/rules/daemon-handling.md safe-imports list)
    from emptyos.sdk.utils import safe_note_filename
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        for title in ["茉莉花", "My: Song/x", "  .  ", "a" * 80]:
            a = srv.safe_filename(d, title, "song")
            b = safe_note_filename(d, title, fallback_prefix="song")
            if title.strip(". ").strip():
                assert a == b, f"safe_filename drifted from SDK for {title!r}: {a!r} != {b!r}"
            else:  # both fall back to random hex — compare the prefix shape only
                assert a.startswith("song-") and b.startswith("song-"), (a, b)
        (d / "dup.md").touch()
        assert srv.safe_filename(d, "dup", "song") == safe_note_filename(d, "dup", fallback_prefix="song") == "dup-2.md"

    # 2. word_count ≍ writing-editor's _word_count
    we = ast.parse(WE_APP.read_text(encoding="utf-8"))
    ns: dict = {"re": __import__("re")}
    for node in we.body:
        if isinstance(node, (ast.Assign, ast.FunctionDef)):
            names = ([t.id for t in node.targets if isinstance(t, ast.Name)]
                     if isinstance(node, ast.Assign) else [node.name])
            if set(names) & {"_CJK_RE", "_word_count"}:
                exec(compile(ast.Module(body=[node], type_ignores=[]), "<we>", "exec"), ns)
    for text in ["第一段。\n第二段 with words.", "hello world", "你好", ""]:
        assert srv.word_count(text) == ns["_word_count"](text), f"word_count drifted for {text!r}"

    # 3. parse_llm_json ≍ emptyos.sdk.utils.parse_llm_json on the success shapes.
    #    (Failure shape differs by design: SDK raises/falls-back, writedesk
    #    returns None — the server treats None as "model gave junk".)
    from emptyos.sdk.utils import parse_llm_json as sdk_pllm
    for raw in ['{"a": 1}', '```json\n{"a": 1}\n```', 'noise {"a": [1, 2]} trailing']:
        assert srv.parse_llm_json(raw) == sdk_pllm(raw), f"parse_llm_json drifted for {raw!r}"
    assert srv.parse_llm_json("not json") is None
    assert sdk_pllm("not json", fallback={}) == {}

    print("shared-helper drift guard: OK")


def extract_prompts() -> dict:
    """Pull the revise/lint prompt constants out of the writing-editor app
    source without importing it (it imports emptyos.sdk)."""
    tree = ast.parse(WE_APP.read_text(encoding="utf-8"))
    wanted = {"REVISE_SYSTEM", "LINT_SYSTEM", "AUDIENCES", "TONES"}
    out: dict = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id in wanted:
            out[node.targets[0].id] = ast.literal_eval(node.value)
    missing = wanted - set(out)
    assert not missing, f"prompt constants missing from app.py: {missing}"
    return out


def build_assets():
    verify_shared_helpers()
    shutil.rmtree(ASSETS, ignore_errors=True)
    (ASSETS / "static").mkdir(parents=True)

    for name in STATIC_FILES:
        shutil.copy2(REPO / "emptyos/web/static" / name, ASSETS / "static" / name)

    # ── writing-editor page: remove compose, PDF button → print view ──
    html = WE_PAGE.read_text(encoding="utf-8")
    html = patch(
        html,
        '<button class="eos-btn eos-btn-sm" onclick="openCompose()" title="Draft a new message from scratch">&#9993; Draft</button>',
        "", "remove Draft button",
    )
    html = patch(html, '<script src="/static/eos-compose.js"></script>', "",
                 "remove compose script")
    html = patch(
        html,
        '<button class="doc-btn" id="doc-pdf-btn" onclick="exportPdf()">&#128196; Export PDF</button>',
        '<button class="doc-btn" id="doc-pdf-btn" onclick="openArticlePrint()">&#128424; Print</button>',
        "PDF button → print view",
    )
    html = patch(
        html,
        "</body>",
        "<script>\n"
        "async function openArticlePrint() {\n"
        "  if (!STATE.file) return;\n"
        "  await flushSave();\n"
        "  window.open('/writing-editor/print.html?file=' + encodeURIComponent(STATE.file), '_blank');\n"
        "}\n"
        "</script>\n</body>",
        "inject openArticlePrint",
    )
    (ASSETS / "writing-editor.html").write_text(html, encoding="utf-8")

    # ── jianpu page: drop the server-PDF button (Print covers it) ──
    html = (JP_DIR / "index.html").read_text(encoding="utf-8")
    html = patch(
        html,
        '<button class="jp-btn" id="jp-pdf-btn" onclick="exportPdf()">&#128196; Export PDF</button>',
        "", "remove jianpu PDF button",
    )
    (ASSETS / "jianpu.html").write_text(html, encoding="utf-8")

    shutil.copy2(JP_DIR / "jianpu-render.js", ASSETS / "jianpu-render.js")
    shutil.copy2(JP_DIR / "print.html", ASSETS / "print.html")
    shutil.copy2(HERE / "pages/home.html", ASSETS / "home.html")
    shutil.copy2(HERE / "pages/article-print.html", ASSETS / "article-print.html")

    (ASSETS / "prompts.json").write_text(
        json.dumps(extract_prompts(), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"assets assembled → {ASSETS}")


def build_exe(console: bool):
    dist = HERE / "dist" / "WriteDesk"
    shutil.rmtree(HERE / "dist", ignore_errors=True)
    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onefile", "--name", "WriteDesk",
        "--distpath", str(dist),
        "--workpath", str(HERE / "build"),
        "--specpath", str(HERE / "build"),
        "--add-data", f"{ASSETS}{';' if sys.platform == 'win32' else ':'}assets",
        "--collect-submodules", "uvicorn",
        "--hidden-import", "multipart",
        "--hidden-import", "python_multipart",
        str(HERE / "launcher.py"),
    ]
    if not console:
        cmd.insert(cmd.index("--onefile"), "--noconsole")
    print(" ".join(cmd))
    subprocess.run(cmd, check=True)

    shutil.copy2(HERE / "config.example.toml", dist / "config.toml")
    shutil.copy2(HERE / "使用说明.txt", dist / "使用说明.txt")
    print(f"\nbuilt → {dist}\\WriteDesk.exe")
    print("Next: fill openai_api_key in config.toml, run once to verify, zip the folder.")


if __name__ == "__main__":
    build_assets()
    if "--assets" not in sys.argv:
        build_exe(console="--console" in sys.argv)
