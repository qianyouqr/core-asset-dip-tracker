@echo off
setlocal

REM Daily scheduled run of core-asset-dip-tracker skill.
REM Invoked by Windows Task Scheduler.

set "PROJECT_DIR=c:\claude code"
set "LOG_DIR=%USERPROFILE%\.claude\skills\core-asset-dip-tracker\state\logs"
set "CLAUDE_EXE=C:\Users\wenlong\AppData\Roaming\npm\claude.cmd"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

REM One log per run, named YYYY-MM-DD. If multiple runs in a day (manual retrigger), they append.
REM Use PowerShell to get an unambiguous date format (DATE format varies by locale).
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "RUN_DATE=%%i"
set "LOG=%LOG_DIR%\%RUN_DATE%.log"

cd /d "%PROJECT_DIR%"

echo. >> "%LOG%"
echo ========== %DATE% %TIME% START ========== >> "%LOG%"
echo cwd: %CD% >> "%LOG%"

REM --output-format text + < NUL: disable Ink TUI, close stdin, avoid headless deadlock
call "%CLAUDE_EXE%" -p "/core-asset-dip-tracker" --permission-mode bypassPermissions --output-format text < NUL >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"

echo ========== %DATE% %TIME% END (exit=%RC%) ========== >> "%LOG%"

endlocal ^& exit /b %RC%
