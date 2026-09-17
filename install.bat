@echo off
chcp 65001 >nul
echo ==============================
echo   Guardian - 安装依赖
echo ==============================
echo.

:: 使用完整 Python 路径
set PYTHON=C:\Users\H\AppData\Local\Programs\Python\Python312\python.exe

:: 尝试用户电脑的 Python
if exist "%PYTHON%" goto :install
set PYTHON=python

:install
echo 安装依赖中...
%PYTHON% -m pip install -r requirements.txt -q
echo.
echo ✅ 安装完成！
echo.
echo 运行方式：
echo   1. 先列出窗口:  run.bat list
echo   2. 配置向导:    run.bat setup
echo   3. 启动监控:    run.bat
echo.
pause
