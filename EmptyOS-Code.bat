@echo off
REM EmptyOS Code (terminal) — review-gated coding REPL against the running daemon.
REM Double-click or pin to taskbar. Needs the :9000 daemon running.
REM This is the keyboard-first sibling of EmptyOS-Code.vbs (which opens the web IDE window).
cd /d "%~dp0"
title EmptyOS Code
python -m emptyos code %*
