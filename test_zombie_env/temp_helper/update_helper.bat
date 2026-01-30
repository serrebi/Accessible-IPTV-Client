@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
cd /d "%TEMP%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%update_helper.ps1" %*
exit /b %ERRORLEVEL%
