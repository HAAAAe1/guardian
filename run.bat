@echo off
chcp 65001 >nul
cd /d "%~dp0"
python guardian_gui.py
if %errorlevel% neq 0 (
    echo.
    echo 启动失败，请先运行 install.bat 安装依赖
    pause
)
