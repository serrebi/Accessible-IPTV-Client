@echo off
echo Cleaning up old build artifacts...
if exist build rd /s /q build
if exist dist rd /s /q dist

echo Running PyInstaller...
pyinstaller --noconfirm main.spec

if %ERRORLEVEL% EQU 0 (
    echo.
    echo Build successful!
    echo Executable can be found in: dist\iptvclient\iptvclient.exe
) else (
    echo.
    echo Build failed!
    exit /b %ERRORLEVEL%
)
