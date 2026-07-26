@echo off
REM Thin wrapper. All supervision logic lives in launcher.py so that it is
REM shared with Linux -- this file only activates the venv and re-runs the
REM launcher when the launcher updates itself (exit 44).

setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
) else if exist "venv\Scripts\activate.bat" (
    call "venv\Scripts\activate.bat"
) else (
    echo ERROR: no virtualenv found ^(.venv\ or venv\^) 1>&2
    exit /b 1
)

:run
python launcher.py
set EXIT_CODE=%ERRORLEVEL%

REM 44 = launcher.py replaced itself during an update; re-run so the new
REM code takes effect. Anything else is final.
if %EXIT_CODE% EQU 44 (
    echo [startup] launcher updated, re-running...
    goto run
)

exit /b %EXIT_CODE%
