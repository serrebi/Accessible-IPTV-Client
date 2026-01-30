@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
cd /d "%TEMP%"

REM Use VBScript to launch PowerShell invisibly
cscript //nologo "%SCRIPT_DIR%update_helper_launcher.vbs" "%SCRIPT_DIR%update_helper.ps1" %*
exit /b 0
