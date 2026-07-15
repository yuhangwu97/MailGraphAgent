@echo off
setlocal

echo ============================================
echo   MailGraphAgent - Setup
echo ============================================
echo.

cd /d "%~dp0.."

:: ---- Check Python ----
echo [1/4] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.12+
    echo         Download: https://www.python.org/downloads/
    echo         IMPORTANT: Check "Add Python to PATH" during install
    pause
    exit /b 1
)
python --version
echo.

:: ---- Create venv ----
echo [2/4] Creating Python virtual environment...
if exist .venv (
    echo         .venv already exists, skipping
) else (
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment
        pause
        exit /b 1
    )
    echo         .venv created
)
echo.

:: ---- Install Python deps ----
echo [3/4] Installing Python dependencies...
call .venv\Scripts\activate.bat
if errorlevel 1 (
    echo [ERROR] Failed to activate virtual environment
    pause
    exit /b 1
)
pip install --upgrade pip --quiet
pip install --index-url https://download.pytorch.org/whl/cpu torch --quiet
pip install -r requirements.txt --quiet
echo         Python dependencies installed
echo.

:: ---- Install frontend deps ----
echo [4/4] Installing frontend dependencies...
if exist src\web\package.json (
    cd src\web
    call npm install --silent
    cd ..\..
    echo         Frontend dependencies installed
) else (
    echo         src\web\package.json not found, skipping
)
echo.

echo ============================================
echo   Setup complete!
echo.
echo   Next steps:
echo   1. Edit .env file with your API keys and email config
echo   2. Run scripts\start.bat to launch services
echo ============================================
pause
