@echo off
setlocal EnableExtensions
rem ============================================================
rem  Piano Sheet Generator - GUI launcher (double-click to run)
rem  If the GUI does not start, run setup.bat once first.
rem ============================================================
cd /d "%~dp0"

rem --- 1) project venv created by setup.bat (preferred) ---
if exist ".venv-score\Scripts\python.exe" (
    set "PYEXE=.venv-score\Scripts\python.exe"
    goto :run
)

rem --- 2) user override via env var ---
if defined PSG_PYTHON_SCORE if exist "%PSG_PYTHON_SCORE%" (
    set "PYEXE=%PSG_PYTHON_SCORE%"
    goto :run
)

rem --- 3) known conda "score" env ---
set "CAND=%USERPROFILE%\AppData\Local\miniconda3\envs\score\python.exe"
if exist "%CAND%" (
    set "PYEXE=%CAND%"
    goto :run
)

rem --- 4) conda base python ---
set "CAND=%USERPROFILE%\AppData\Local\miniconda3\python.exe"
if exist "%CAND%" (
    set "PYEXE=%CAND%"
    goto :run
)

rem --- 5) python / py launcher on PATH ---
where python >nul 2>nul && (
    set "PYEXE=python"
    goto :run
)
where py >nul 2>nul && (
    set "PYEXE=py -3"
    goto :run
)

echo [ERROR] Python not found.
echo Please run setup.bat first, or install Python 3.10-3.12.
pause
exit /b 1

:run
echo Starting Piano Sheet Generator ...
echo Python: %PYEXE%
echo.
%PYEXE% "%~dp0run.py"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [ERROR] Exit code %RC%. See messages above or README.md.
    echo If dependencies are missing, run setup.bat first.
    pause
)
endlocal
