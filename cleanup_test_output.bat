@echo off
setlocal EnableExtensions
rem ============================================================
rem  Cleanup test-generated output (run as ADMINISTRATOR).
rem
rem  Why this script exists:
rem    The two test PDFs under output/ were created with an NTFS
rem    ACL that denies the current standard user any access, so a
rem    normal delete / takeown / icacls all fail with Access Denied.
rem    Taking ownership requires elevation -> run this as admin.
rem
rem  How to use:
rem    Right-click this file -> "Run as administrator".
rem    It keeps output/.gitkeep and removes everything else.
rem ============================================================
cd /d "%~dp0"

net session >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Administrator rights required.
    echo Right-click this file and choose "Run as administrator".
    pause
    exit /b 1
)

set "OUT=%~dp0output"
echo Cleaning test output in: %OUT%
echo.

rem take ownership of everything under output, then grant full control
takeown /f "%OUT%" /r /d y >nul 2>nul
icacls "%OUT%" /grant *S-1-5-32-544:F /t /c >nul 2>nul
icacls "%OUT%" /grant "%USERNAME%":F /t /c >nul 2>nul

rem delete all children except .gitkeep
for /d %%D in ("%OUT%\*") do rd /s /q "%%D"
for %%F in ("%OUT%\*") do if /i not "%%~nxF"==".gitkeep" del /f /q "%%F"

echo.
echo Remaining in output:
dir /b "%OUT%"
echo.
echo Done. output/ now contains only .gitkeep
pause
endlocal
