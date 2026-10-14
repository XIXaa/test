@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 pause & exit /b 1
python -m PyInstaller --onefile --windowed --name MultiLiveDesktop --collect-all customtkinter --noconfirm --clean main.py
if errorlevel 1 pause & exit /b 1
echo EXE created at dist\MultiLiveDesktop.exe
pause
