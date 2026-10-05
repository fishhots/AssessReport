@echo off
setlocal
chcp 65001 >nul
title 教学质量报告智能体 - 停止
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0manage-system.ps1" -Action Stop
if errorlevel 1 (
    echo.
    echo 停止未完成，请查看上面的提示。
    pause
    exit /b 1
)
exit /b 0
