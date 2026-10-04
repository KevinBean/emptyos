---
paths:
  - "plugins/**/plugin.py"
  - "restart.bat"
  - "stop.bat"
---

# External Service Launch Pattern

Moved out of CLAUDE.md (2026-09-25). External services (Ollama, ComfyUI,
voice-api, Blender) use embedded runtimes with relative paths. Two launch
contexts, same rules:

**restart.bat** — `pushd` + `start /b` for headless background:
```batch
pushd D:\ComfyUI_windows_portable
start /b "" .\python_embeded\python.exe -s ComfyUI\main.py --windows-standalone-build >nul 2>nul
popd
```

**Plugin `auto_start()`** — embedded python directly with `CREATE_NO_WINDOW`:
```python
subprocess.Popen([python_exe, "-s", main_py, "--flags"],
                 cwd=launcher_dir, creationflags=subprocess.CREATE_NO_WINDOW,
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
```

Rules:
- **Never** `start /min` or `cmd /c start` (both open windows)
- **Always** set `cwd` to the service directory — launcher scripts use relative paths
- Config: `emptyos.toml` `[plugins.<id>]` `launcher = "path/to/run.bat"`
- Health check: poll service endpoint (e.g. `/system_stats`) until ready, 60s timeout
