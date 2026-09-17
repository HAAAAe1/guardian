@echo off
chcp 65001 >nul
title Guardian - 安装依赖
echo.
echo ==============================
echo   Guardian - 依赖安装
echo ==============================
echo.

:: 检查 Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未检测到 Python！
    echo 请先安装 Python 3.10+：https://www.python.org/downloads/
    echo 安装时请勾选 "Add Python to PATH"
    echo.
    pause
    exit /b 1
)

echo [1/2] 安装依赖中...
python -m pip install -r requirements.txt -q
if %errorlevel% neq 0 (
    echo.
    echo [警告] 部分依赖安装失败，尝试使用镜像源...
    python -m pip install -r requirements.txt -q -i https://pypi.tuna.tsinghua.edu.cn/simple
)

echo.
echo [2/2] 检查安装结果...
python -c "import cv2, ultralytics, pystray; print('所有依赖安装成功！')" 2>nul
if %errorlevel% neq 0 (
    echo.
    echo [错误] 依赖安装不完整，请检查网络后重试
    pause
    exit /b 1
)

echo.
echo ==============================
echo   安装完成！
echo ==============================
echo.
echo 双击 run.bat 即可启动 Guardian
echo.
pause
