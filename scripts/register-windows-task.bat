@echo off
setlocal

REM 一键注册 Windows 任务计划：每天 08:30 调起 core-asset-dip-tracker。
REM 用法：双击运行（无需管理员，注册为当前用户的任务）。

set "TASKNAME=CoreAssetDipTracker"
set "RUNNER=%USERPROFILE%\.claude\skills\core-asset-dip-tracker\scripts\run-daily.bat"
set "SCHEDULE_TIME=08:30"

if not exist "%RUNNER%" (
    echo [错误] 找不到 run-daily.bat：%RUNNER%
    pause
    exit /b 1
)

echo 准备注册任务 %TASKNAME%
echo   触发时间：每天 %SCHEDULE_TIME%（本地时间）
echo   执行脚本：%RUNNER%
echo.

schtasks /Create /SC DAILY /TN "%TASKNAME%" /TR "%RUNNER%" /ST %SCHEDULE_TIME% /F

if %ERRORLEVEL% EQU 0 (
    echo.
    echo [成功] 任务已注册。
    echo.
    echo 常用命令：
    echo   查看:   schtasks /Query /TN "%TASKNAME%"
    echo   立即运行: schtasks /Run /TN "%TASKNAME%"
    echo   删除:   schtasks /Delete /TN "%TASKNAME%" /F
    echo   改时间: schtasks /Change /TN "%TASKNAME%" /ST 07:30
    echo.
    echo 日志目录：%USERPROFILE%\.claude\skills\core-asset-dip-tracker\state\logs\
) else (
    echo.
    echo [失败] schtasks 返回 %ERRORLEVEL%
    echo 可能原因：当前用户没有创建任务权限，或任务名已存在。
    echo 手动命令：
    echo   schtasks /Create /SC DAILY /TN "%TASKNAME%" /TR "%RUNNER%" /ST %SCHEDULE_TIME% /F
)

echo.
pause
endlocal
