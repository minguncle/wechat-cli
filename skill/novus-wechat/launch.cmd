@echo off
setlocal
set "ROOT=%~dp0"
if not exist "%ROOT%runtime\windows-x64\python.exe" (
  echo Missing bundled Windows runtime. Use the complete Skill release package.
  exit /b 1
)
"%ROOT%runtime\windows-x64\python.exe" -X utf8 "%ROOT%boot.py" %*
