@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  set "ML_PY=py -3"
) else (
  set "ML_PY=python"
)
%ML_PY% -c "import customtkinter, PIL" >nul 2>nul
if errorlevel 1 (
  echo Installing user-interface dependencies...
  %ML_PY% -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Dependency installation failed.
    pause
    exit /b 1
  )
)
%ML_PY% main.py
if errorlevel 1 pause
