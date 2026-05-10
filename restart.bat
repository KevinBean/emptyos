@echo off
echo ============================================
echo   EmptyOS Restart
echo ============================================

echo.
echo [1/3] Stopping processes...
taskkill /F /IM python.exe 2>nul
timeout /t 2 /nobreak >nul

echo.
echo [2/3] Checking services...

REM Check Ollama (separate process, not killed by taskkill python.exe)
curl -s http://localhost:11434/api/tags >nul 2>nul
if %errorlevel%==0 (
    echo   Ollama: OK
) else (
    echo   Ollama: Starting...
    start /min "" ollama serve
    timeout /t 3 /nobreak >nul
)

REM Check ComfyUI — runs under its own python.exe, killed by taskkill above.
REM Restart headless via the embedded interpreter (no extra window).
curl -s http://localhost:8188/system_stats >nul 2>nul
if %errorlevel%==0 (
    echo   ComfyUI: OK
) else (
    echo   ComfyUI: Starting...
    pushd D:\ComfyUI_windows_portable
    start /b "" .\python_embeded\python.exe -s ComfyUI\main.py --windows-standalone-build >nul 2>nul
    popd
    timeout /t 5 /nobreak >nul
)

REM Check Voice API (port 8602) — also python.exe, killed above.
REM Fingerprint the response so a stray service doesn't fool us.
curl -s http://localhost:8602/health 2>nul | findstr /C:"edge_voices" >nul 2>nul
if %errorlevel%==0 (
    echo   Voice API: OK
) else (
    echo   Voice API: Starting on 8602...
    pushd "%~dp0services\voice-api"
    set VOICE_API_PORT=8602
    start /b "" python server.py >nul 2>nul
    popd
    timeout /t 3 /nobreak >nul
)

echo.
echo [3/3] Starting EmptyOS...
REM %~dp0 is the directory of this .bat file (with trailing backslash) — keeps
REM the script portable for fresh clones at any path, not just D:\emptyos.
cd /d "%~dp0"

REM The dogfood :9001 sidecar is owned by plugins/dogfood-demo/ now and
REM auto-starts when the main daemon's kernel boots — same lifecycle as
REM ComfyUI/voice-api/Ollama. No separate starter here. To enable it,
REM copy dogfood/emptyos.toml.example to dogfood/emptyos.toml and set
REM `[plugins.dogfood-demo] enabled = true` in your top-level emptyos.toml.

REM Main daemon (foreground; Ctrl+C stops main + plugin-spawned children).
python -m emptyos start
