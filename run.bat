@echo off
cd /d "%~dp0"
set PYTHON=C:\Users\H\AppData\Local\Programs\Python\Python312\pythonw.exe
if not exist "%PYTHON%" set PYTHON=pythonw
start "" "%PYTHON%" guardian_gui.py
