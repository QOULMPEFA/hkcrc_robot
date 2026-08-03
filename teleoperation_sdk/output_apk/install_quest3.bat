@echo off
REM ============================================
REM  Install client_quest3_test.apk to Quest 3
REM  Usage: double-click, or run in cmd/PowerShell
REM ============================================
set ADB=%~dp0..\tools\adb.exe
set APK=%~dp0client_quest3_test.apk

if not exist "%APK%" (
    echo [ERROR] APK not found: %APK%
    exit /b 1
)

echo [1/2] Checking device...
%ADB% devices

echo [2/2] Installing APK...
%ADB% install -r "%APK%"
if %errorlevel%==0 (
    echo.
    echo [OK] Install success.
) else (
    echo.
    echo [FAIL] Install failed. Check:
    echo   - USB cable connected
    echo   - Device authorized ^(accept "Allow USB debugging" dialog^)
    echo   - Developer Mode enabled in Meta Horizon app
    exit /b 1
)
