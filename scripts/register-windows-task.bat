@echo off
setlocal

REM One-shot: register a Windows scheduled task that triggers core-asset-dip-tracker daily.
REM Usage: double-click this file (no admin needed; task runs under current user).
REM
REM Override the trigger time (default 08:30):
REM   set SCHEDULE_TIME=07:30 ^&^& register-windows-task.bat
REM Override the task name (useful for parallel test tasks):
REM   set TASKNAME=CoreAssetDipTracker_Test ^&^& register-windows-task.bat

if not defined TASKNAME      set "TASKNAME=CoreAssetDipTracker"
if not defined SCHEDULE_TIME set "SCHEDULE_TIME=08:30"

REM Derive RUNNER from this script's own location.
set "SCRIPT_DIR=%~dp0"
set "RUNNER=%SCRIPT_DIR%run-daily.bat"

if not exist "%RUNNER%" (
    echo [error] run-daily.bat not found: %RUNNER%
    pause
    exit /b 1
)

echo Registering task %TASKNAME%
echo   Trigger:    daily at %SCHEDULE_TIME% (local time)
echo   Runner:     %RUNNER%
echo.

schtasks /Create /SC DAILY /TN "%TASKNAME%" /TR "\"%RUNNER%\"" /ST %SCHEDULE_TIME% /F

if %ERRORLEVEL% EQU 0 (
    echo.
    echo [ok] Task registered.
    echo.
    echo Common commands:
    echo   View:       schtasks /Query /TN "%TASKNAME%" /V /FO LIST
    echo   Run now:    schtasks /Run /TN "%TASKNAME%"
    echo   Delete:     schtasks /Delete /TN "%TASKNAME%" /F
    echo   Change:     schtasks /Change /TN "%TASKNAME%" /ST 07:30
    echo.
    echo Log dir:      %SCRIPT_DIR%..\state\logs\
) else (
    echo.
    echo [fail] schtasks returned %ERRORLEVEL%
    echo Possible causes: insufficient privilege, or task name already exists.
    echo Manual command:
    echo   schtasks /Create /SC DAILY /TN "%TASKNAME%" /TR "\"%RUNNER%\"" /ST %SCHEDULE_TIME% /F
)

echo.
pause
endlocal
