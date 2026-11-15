@echo off
rem AgentCAD を起動する（ブラウザが開く）。例: run.bat --demo
cd /d "%~dp0core"
python -m cad.api %*
