@echo off
REM TWB v1.1 — Windows Setup Launcher
REM Runs the cross-platform Python setup script

echo ============================================================
echo  TWB v1.1 - Offline Medical Q&A Pipeline Setup (Windows)
echo ============================================================
echo.

REM Check for Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found in PATH
    echo Please install Python 3.11+ from https://python.org
    echo Make sure to check "Add Python to PATH" during installation
    pause
    exit /b 1
)

REM Check Python version
for /f "tokens=2" %%a in ('python --version') do set PY_VER=%%a
echo Found Python %PY_VER%

REM Run the setup script
python setup.py

echo.
pause