@echo off
setlocal

REM Detect elevation up front. We do NOT self-elevate unconditionally — that
REM would force a UAC prompt on every restart and push the daemon into a
REM separate elevated console, breaking the WezTerm in-tab cockpit (Ctrl+Shift+R
REM runs the daemon in the foreground of its tab). Instead we try a normal kill
REM first and only relaunch as admin if the daemon SURVIVES it (it was running
REM elevated and a non-elevated taskkill couldn't reach it — the WinError 10048
REM case). `net session` returns errorlevel 0 only when already elevated.
net session >nul 2>&1
set _elevated=0
if %errorlevel%==0 set _elevated=1

echo ============================================
echo   EmptyOS Restart
echo ============================================

echo.
echo [1/3] Stopping processes...
REM Kill the watchdog window FIRST. In --restart mode it respawns the daemon
REM detached; if it survives the python kill below it can re-grab :9000 during
REM the gap before our own start binds. Title set by the `start "EmptyOS
REM Watchdog"` line further down.
taskkill /F /FI "WINDOWTITLE eq EmptyOS Watchdog" >nul 2>nul
taskkill /F /IM python.exe 2>nul
timeout /t 2 /nobreak >nul

REM If :9000 is STILL held after the kill, the daemon ignored it — it's running
REM at admin integrity a non-elevated taskkill can't touch. Relaunch as admin
REM (one UAC prompt) so the kill can land; the elevated copy re-runs from the
REM top. When already elevated we skip this and the port-free guard below
REM force-kills the listener by PID. The common case (a non-elevated daemon)
REM frees the port here and never elevates, so a WezTerm Ctrl+Shift+R keeps
REM running the daemon in-tab with no UAC prompt.
netstat -ano | findstr /R /C:":9000 .*LISTENING" >nul 2>nul
if not errorlevel 1 (
    if %_elevated%==0 (
        echo   Daemon survived the kill - it is running elevated. Relaunching as admin...
        powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs -WorkingDirectory '%~dp0'"
        exit /b
    )
)

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
    REM ComfyUI keeps its own timestamped log (ComfyUI\user\comfyui.log, 3
    REM generations) and the per-prompt timings live there — do not re-derive a
    REM baseline from this file. What this one adds is the boot and crash output
    REM that precedes that logger, which >nul threw away. Rotate to bound it,
    REM then APPEND: the comfyui plugin's auto_start() writes the same path when
    REM it revives a dead ComfyUI, and truncating there would erase this boot.
    if exist comfyui.log move /y comfyui.log comfyui.prev.log >nul 2>nul
    start /b "" .\python_embeded\python.exe -s ComfyUI\main.py --windows-standalone-build >>comfyui.log 2>&1
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

REM Check Pronounce API (port 8603) — phoneme-scoring service. Model is lazy-
REM loaded on first /score, so booting here just gets the listener ready; the
REM plugin re-spawns if missing on daemon boot. Fingerprint on the model id.
curl -s http://localhost:8603/health 2>nul | findstr /C:"wav2vec2-xlsr" >nul 2>nul
if %errorlevel%==0 (
    echo   Pronounce API: OK
) else (
    echo   Pronounce API: Starting on 8603...
    pushd "%~dp0services\pronounce"
    set PRONOUNCE_API_PORT=8603
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

REM Clear any stale wedge-alert flag from a previous boot — the upcoming
REM session starts clean. The watchdog re-writes the flag only on a real
REM mid-runtime wedge.
if exist data\wedge-alert.flag del data\wedge-alert.flag >nul 2>nul

REM Daemon watchdog — separate window so Ctrl+C of the main daemon below
REM doesn't take it down with the same console group. Watchdog has its own
REM boot-grace: it won't capture evidence until the daemon has been healthy
REM at least once, so the 70-80s boot window won't trip a false positive.
REM Next restart.bat kills + relaunches it (it's python.exe, so the taskkill
REM above gets it). On wedge: snapshots data/wedge-evidence/<ts>/ + writes
REM data/wedge-alert.flag + telegram push (if [plugins.telegram] configured).
REM --restart turns on crash/wedge recovery: after capturing evidence it
REM targeted-kills the wedged daemon's PID tree + respawns it detached, with a
REM storm guard (max 3 restarts / 30 min, then it gives up + alerts). Drop
REM --restart for evidence-only (the diagnostic default).
REM Guard: confirm nothing still holds :9000 before we start. After the
REM elevated taskkill above this should already be free; this catches a
REM lingering elevated listener or a slow-dying process and kills it by PID,
REM rather than dying with WinError 10048 on bind. Matches only the LISTENING
REM socket — TIME_WAIT/ESTABLISHED entries don't block a new listener.
echo   Ensuring port 9000 is free...
set _waited=0
:portcheck
netstat -ano | findstr /R /C:":9000 .*LISTENING" >nul 2>nul
if errorlevel 1 goto portfree
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":9000 .*LISTENING"') do taskkill /F /PID %%P >nul 2>nul
timeout /t 1 /nobreak >nul
set /a _waited+=1
if %_waited% lss 20 goto portcheck
echo   WARNING: port 9000 still appears bound after 20s; starting anyway.
:portfree
echo   Port 9000 is free.

REM Prefer the durable Scheduled Task watchdog (scripts\install_watchdog_task.ps1)
REM — it survives a closed WezTerm tab / logoff / reboot and runs elevated, so it
REM can kill an elevated :9000. We /End then /Run so a fresh instance picks up any
REM code change and resumes recovery immediately (instead of waiting for the
REM task's 5-min relaunch tick). Launching ONLY the task here (not also the window
REM below) avoids two watchdogs racing on the recovery lock — a non-elevated
REM window watchdog could grab the lock and then fail to kill the elevated daemon.
REM If the task isn't installed, fall back to the per-session minimized window.
schtasks /query /TN "EmptyOS Watchdog" >nul 2>nul
if not errorlevel 1 (
    schtasks /End /TN "EmptyOS Watchdog" >nul 2>nul
    schtasks /Run /TN "EmptyOS Watchdog" >nul 2>nul
    echo   Watchdog: kicked durable Scheduled Task ^(survives terminal/logoff/reboot^).
) else (
    echo   Watchdog: no Scheduled Task installed - using per-session window.
    echo            Run scripts\install_watchdog_task.ps1 once for durable recovery.
    start "EmptyOS Watchdog" /min cmd /c python scripts\daemon_watchdog.py --restart
)

REM Probe :9000/:9001/:9100 in the background while the foreground daemon
REM boots. Results print into this console; :9001 is optional and never makes
REM the restart fail. The main daemon's plugins own restoration of both sidecars.
start "" /b python scripts\restart_health_check.py

REM Main daemon (foreground; Ctrl+C triggers graceful plugin child shutdown).
python -m emptyos start
