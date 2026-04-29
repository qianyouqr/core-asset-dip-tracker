@echo off
setlocal

REM Daily scheduled run of core-asset-dip-tracker skill.
REM Invoked by Windows Task Scheduler.
REM
REM All paths are derived from %~dp0 so the script is portable across machines.
REM Controlled by config/config.json -> scheduler.enabled (false to skip).

set "SCRIPT_DIR=%~dp0"
set "SKILL_ROOT=%SCRIPT_DIR%.."
set "LOG_DIR=%SKILL_ROOT%\state\logs"
set "CONFIG_FILE=%SKILL_ROOT%\config\config.json"

REM Prefer claude.cmd from PATH; fall back to %APPDATA%\npm
where claude.cmd >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set "CLAUDE_EXE=claude.cmd"
) else if exist "%APPDATA%\npm\claude.cmd" (
    set "CLAUDE_EXE=%APPDATA%\npm\claude.cmd"
) else (
    echo [error] claude.cmd not found. Run: npm i -g @anthropic-ai/claude-code 1>&2
    exit /b 127
)

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "RUN_DATE=%%i"
set "LOG=%LOG_DIR%\%RUN_DATE%.log"

REM Read scheduler.enabled from config.json; if false, skip the run.
REM Avoid '|' inside the PS command to dodge cmd's ^| escape quirks in for /f.
set "SCHED_ENABLED=true"
for /f "delims=" %%i in ('powershell -NoProfile -Command "try { ([string](ConvertFrom-Json (Get-Content -Raw -Encoding utf8 '%CONFIG_FILE%')).scheduler.enabled).ToLower() } catch { 'true' }"') do set "SCHED_ENABLED=%%i"

REM Safety: if PS still produced something unexpected, force back to 'true' (don't skip).
echo %SCHED_ENABLED%| findstr /I /R "^true$ ^false$" >nul || set "SCHED_ENABLED=true"

if /I "%SCHED_ENABLED%"=="false" (
    echo. >> "%LOG%"
    echo ========== %DATE% %TIME% SKIPPED ^(scheduler.enabled=false^) ========== >> "%LOG%"
    endlocal ^& exit /b 0
)

cd /d "%SKILL_ROOT%"

echo. >> "%LOG%"
echo ========== %DATE% %TIME% START ========== >> "%LOG%"
echo cwd: %CD% >> "%LOG%"

REM --output-format text + < NUL: disable Ink TUI, close stdin, avoid headless deadlock
call "%CLAUDE_EXE%" -p "/core-asset-dip-tracker" --permission-mode bypassPermissions --output-format text < NUL >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"

echo ========== %DATE% %TIME% END ^(exit=%RC%^) ========== >> "%LOG%"

endlocal ^& exit /b %RC%
