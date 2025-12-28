@echo off
setlocal

set "MODE=%~1"
if "%MODE%"=="" set "MODE=build"

if /I "%MODE%"=="build" goto :run
if /I "%MODE%"=="release" goto :run
if /I "%MODE%"=="dry-run" goto :run

echo Usage: build_exe.bat [build^|release^|dry-run]
exit /b 1

:run
set "PYTHON=%PYTHON%"
if "%PYTHON%"=="" set "PYTHON=python"
set "SCRIPT=%~dp0tools\release.py"

if not exist "%SCRIPT%" (
    echo Release script not found: %SCRIPT%
    exit /b 1
)

"%PYTHON%" "%SCRIPT%" %MODE%
if %ERRORLEVEL% NEQ 0 exit /b %ERRORLEVEL%

if /I "%MODE%"=="build" (
    echo.
    echo Build successful!
    echo Executable can be found in: dist\iptvclient\IPTVClient.exe
)
