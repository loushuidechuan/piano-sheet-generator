@echo off
setlocal EnableExtensions
rem ============================================================
rem  Piano Sheet Generator - environment setup (Windows)
rem  Usage:  setup.bat            full setup (core + separation)
rem          setup.bat --core-only   main env only
rem          setup.bat --cpu         separation env with CPU torch
rem ============================================================
cd /d "%~dp0"

rem --- prefer existing project venv python for bootstrapping ---
set "BOOT="
if exist ".venv-score\Scripts\python.exe" set "BOOT=.venv-score\Scripts\python.exe"
if defined PSG_PYTHON_SCORE if exist "%PSG_PYTHON_SCORE%" set "BOOT=%PSG_PYTHON_SCORE%"

if defined BOOT goto :boot

rem --- find any python on system for bootstrapping ---
for %%V in (3.12 3.11 3.10) do (
    py -%%V -c "import sys" >nul 2>nul && (
        for /f "delims=" %%P in ('py -%%V -c "import sys;print(sys.executable)"') do set "BOOT=%%P"
        goto :boot
    )
)
where python >nul 2>nul && set "BOOT=python"
if defined BOOT goto :boot

echo [ERROR] No Python found.
echo Please install Python 3.10-3.12:  winget install Python.Python.3.11
pause
exit /b 1

:boot
echo Bootstrap python: %BOOT%
%BOOT% "setup\install.py" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [ERROR] Setup failed with exit code %RC%. See messages above.
    pause
)
endlocal
