@echo off
echo Stopping EmptyOS daemon (port 9000)...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":9000 "') do (
    echo   Killing PID %%p
    taskkill /F /PID %%p 2>nul
)
set /a _tries=0
:wait_port
netstat -ano | findstr "LISTENING" | findstr ":9000 " >nul 2>nul
if %errorlevel%==0 (
    set /a _tries+=1
    if %_tries% GEQ 10 (
        echo   WARNING: port 9000 still in use after 10s
        goto done
    )
    timeout /t 1 /nobreak >nul
    goto wait_port
)
:done
if exist "%~dp0data\syslog.db-wal" del "%~dp0data\syslog.db-wal" 2>nul
if exist "%~dp0data\syslog.db-shm" del "%~dp0data\syslog.db-shm" 2>nul
if exist "%~dp0data\events.db-wal" del "%~dp0data\events.db-wal" 2>nul
if exist "%~dp0data\events.db-shm" del "%~dp0data\events.db-shm" 2>nul
echo Stopped.
