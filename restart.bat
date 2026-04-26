@echo off
echo ============================================
echo   EmptyOS Restart
echo ============================================

echo.
echo [1/3] Stopping EmptyOS...
REM Kill only the process listening on port 9000
for /f "tokens=5" %%p in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":9000 "') do (
    echo   Killing PID %%p (port 9000)
    taskkill /F /PID %%p 2>nul
)
REM Wait until port 9000 is actually free (up to 15 seconds)
set /a _tries=0
:wait_port
netstat -ano | findstr "LISTENING" | findstr ":9000 " >nul 2>nul
if %errorlevel%==0 (
    set /a _tries+=1
    if %_tries% GEQ 15 (
        echo   WARNING: port 9000 still in use after 15s
        goto done_wait
    )
    timeout /t 1 /nobreak >nul
    goto wait_port
)
:done_wait
REM If port still in use, force-kill ALL python on 9000
netstat -ano | findstr "LISTENING" | findstr ":9000 " >nul 2>nul
if %errorlevel%==0 (
    echo   Force-killing remaining processes...
    for /f "tokens=5" %%p in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":9000 "') do (
        taskkill /F /PID %%p 2>nul
    )
    timeout /t 3 /nobreak >nul
)
REM Clean up SQLite WAL/SHM lock files left by force-kill
if exist "%~dp0data\syslog.db-wal" del "%~dp0data\syslog.db-wal" 2>nul
if exist "%~dp0data\syslog.db-shm" del "%~dp0data\syslog.db-shm" 2>nul
if exist "%~dp0data\events.db-wal" del "%~dp0data\events.db-wal" 2>nul
if exist "%~dp0data\events.db-shm" del "%~dp0data\events.db-shm" 2>nul
for /r "%~dp0data\apps" %%f in (*.db-wal *.db-shm) do del "%%f" 2>nul

echo.
echo [2/3] Checking services...

REM Check Ollama
curl -s http://localhost:11434/api/tags >nul 2>nul
if %errorlevel%==0 (
    echo   Ollama: OK
) else (
    echo   Ollama: Starting...
    start /min "" ollama serve
    timeout /t 3 /nobreak >nul
)

REM Check ComfyUI — launch headless (no extra window)
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

REM Check Voice API (EmptyOS embedded, port 8602) — 8601 is legacy home-portal
REM Fingerprint our server via the "edge_voices" key so a stray service doesn't fool us
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
echo [3/3] Starting EmptyOS (headless)...
cd /d "%~dp0"
if not exist "%~dp0data" mkdir "%~dp0data"
REM Launch hidden via PowerShell so the daemon runs under python.exe (not pythonw.exe).
REM pythonw.exe matches a Windows Firewall Block rule that drops inbound from
REM the Tailscale interface, breaking phone access. python.exe is allowed.
powershell -NoProfile -WindowStyle Hidden -Command "Start-Process -WindowStyle Hidden -FilePath 'python.exe' -ArgumentList '-m','emptyos','start' -RedirectStandardOutput '%~dp0data\daemon.log' -RedirectStandardError '%~dp0data\daemon.err.log' -WorkingDirectory '%~dp0'"
echo   Daemon launched. Log: data\daemon.log

REM Tailscale Serve: expose daemon over HTTPS at *.ts.net so phones can use
REM mic/camera APIs (browsers gate getUserMedia behind secure context).
REM Idempotent — applying the same config is a no-op.
where tailscale >nul 2>nul
if %errorlevel%==0 (
    tailscale serve --bg http://localhost:9000 >nul 2>nul
    if %errorlevel%==0 (
        for /f "tokens=1" %%u in ('tailscale serve status ^| findstr /R "^https://"') do echo   Tailscale Serve: %%u
    ) else (
        echo   Tailscale Serve: not configured ^(run once manually if phone access needed^)
    )
)

echo   Stop: stop.bat  ·  Tray menu: right-click EmptyOS icon
timeout /t 2 /nobreak >nul
