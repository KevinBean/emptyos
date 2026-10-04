@echo off
cd /d "%~dp0"
python -m emptyos start >> emptyos.log 2>&1
