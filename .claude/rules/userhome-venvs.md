---
paths:
  - "emptyos/sdk/userhome_venv.py"
  - "plugins/cadquery/**"
  - "plugins/markitdown/**"
  - "plugins/legacy-doc/**"
  - "scripts/eos_desktop.py"
  - "tests/test_unit_desktop_shell.py"
---

## User-home Python envs for heavy / version-pinned deps

Never embed a venv in `D:/emptyos/`. When a tool needs a Python version or
deps that conflict with the daemon's 3.13, install into a user-home venv and
shell out from the plugin.

```bash
# One-time setup per tool
uv venv --python 3.12 "$LOCALAPPDATA/eos/envs/<tool>-3.12"
uv pip install --python "$LOCALAPPDATA/eos/envs/<tool>-3.12/Scripts/python.exe" <packages>
```

Plugin reads the python path from `emptyos.toml` `[plugins.<tool>] python_exe = "..."`.
Shared plumbing lives in `emptyos/sdk/userhome_venv.py` — `default_venv_python(tool, pyver)`,
`probe_launch(exe, args)`, `run_venv(exe, args) -> RunResult`. Stateless functions
(not a base class), so each plugin keeps its own `_launch_ok`/`_launch_err` + domain
error strings. A third venv-backed plugin should reuse these, not re-roll the subprocess plumbing.
First consumer: `plugins/cadquery/` (cadquery + trimesh + vtk in
`%LOCALAPPDATA%/eos/envs/cadquery-3.12/`). Verified 2026-05-23.
Second consumer: `plugins/markitdown/` (`markitdown[pptx,xlsx,xls,docx]` in
`%LOCALAPPDATA%/eos/envs/markitdown-3.13/`) — markitdown's core `magika` dep
pins `onnxruntime<=1.20.1` on win32, which conflicts with the daemon env's
`onnxruntime-gpu 1.23.2`. Installing it in-process downgrades/breaks the GPU
runtime, so it runs in an isolated venv reached via subprocess. Verified
2026-06-07. (Note: the venv can be 3.13 here — markitdown only needs ≥3.10;
the venv is for the onnxruntime *version* conflict, not a Python-version gap.)
Related, but **not** a consumer of `userhome_venv.py`: the **desktop shell**
(`pywebview==6.2.1` + pythonnet + `pystray` + `Pillow` in
`%LOCALAPPDATA%/eos/envs/desktop-3.13/`, or `$EOS_DESKTOP_PYTHON`; without
pystray the shell has no tray and closing its window quits). The daemon never reaches it via subprocess — it is the
shell's *own* interpreter, and it cannot import the SDK, so
`shell_core.default_desktop_python` is a deliberate copy of the path rule.
`scripts/eos_desktop.py --webview` re-execs itself there under `pythonw.exe`.
That matters because the shell's offline splash can run `restart.bat`, which
kills by image name `python.exe` (`pythonw.exe` is spared); the Start button is
therefore offered only when the running image is not `python.exe`
(`shell_core.can_offer_start`). The shell modules (`shell_core.py`,
`single_instance.py`, `shell.py`, `tray.py`) must never import `emptyos.sdk` —
that venv lacks the daemon's deps (pinned by `tests/test_unit_desktop_shell.py`).
Verified 2026-09-11 on pywebview 6.2.1 / pythonnet 3.1.0 / CPython 3.13.5.

Why not embedded: keeps repo clean, survives `git clean -fdx`, shared across
checkouts, never gets into `release-public.py` snapshots.

Why not switching EmptyOS to 3.12: daemon's own deps (FastAPI, plugins,
agent-runtime) have settled on 3.13.

`py -0p` lists installed Pythons. `uv` is on PATH at
`%APPDATA%\Python\Python313\Scripts\uv.exe`.

See `reference_userhome_python_envs_for_heavy_deps` memory for cross-references.

