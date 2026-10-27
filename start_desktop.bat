@echo off
chcp 65001 >nul
title DivergenceQuant 桌面端量化交易系统

echo ===================================================
echo   CHRONO QUANT / DivergenceQuant 顶级量化终端
echo   前端 + 后端 + 数据库三合一一体化桌面系统
echo ===================================================
echo.

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"
set "PYTHONPATH=%SCRIPT_DIR%src"

echo [1/3] 正在检测运行环境...
where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    set "PY_CMD=py"
) else (
    where python >nul 2>nul
    if %ERRORLEVEL% EQU 0 (
        set "PY_CMD=python"
    ) else (
        echo [错误] 未找到 Python 运行环境，请先安装 Python 3.11+ 并加入 PATH。
        pause
        exit /b 1
    )
)

echo [2/3] 检查并准备本地 SQLite 数据目录 (data/)...
if not exist "data" mkdir "data"

echo [3/3] 启动量化终端主程序 (desktop_app.py)...
echo.
%PY_CMD% desktop_app.py %*

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [提示] 应用程序退出 (退出码: %ERRORLEVEL%)
    pause
)
