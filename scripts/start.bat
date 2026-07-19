@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0.."
set ROOT=%CD%
title MailGraphAgent - Start

echo ============================================
echo   MailGraphAgent - Start
echo ============================================
echo.
echo   Root: %ROOT%
echo.

:: ---- Check venv ----
if not exist "%ROOT%\.venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Please run scripts\setup.bat first.
    pause
    exit /b 1
)

:: ---- Check & install deps if needed ----
"%ROOT%\.venv\Scripts\python.exe" -c "import uvicorn" >nul 2>&1
if errorlevel 1 (
    echo [!] Dependencies not installed, installing now...
    "%ROOT%\.venv\Scripts\python.exe" -m pip install --index-url https://download.pytorch.org/whl/cpu torch
    "%ROOT%\.venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] pip install failed. Check the output above.
        pause
        exit /b 1
    )
    echo.
)

if not exist "%ROOT%\src\web\node_modules" (
    echo [!] Frontend deps not installed, installing now...
    cd /d "%ROOT%\src\web"
    call npm install
    cd /d "%ROOT%"
    echo.
)

echo ============================================
echo   Starting services...
echo ============================================
echo.

echo [1/3] Starting API (port 8000)...
start "MailGraph API" cmd /k "cd /d %ROOT% && set PYTHONPATH=%ROOT% && %ROOT%\.venv\Scripts\python.exe -m uvicorn src.backend.app:app --host 0.0.0.0 --port 8000"

echo        Waiting for API...
:wait_api
curl -s http://localhost:8000/api/status
echo.
if errorlevel 1 (
    timeout /t 2 >nul
    goto wait_api
)
echo        API         [OK]
echo.

echo [2/3] Starting Workers (x5)...
start "MailGraph Worker 1" cmd /k "cd /d %ROOT% && set PYTHONPATH=%ROOT% && %ROOT%\.venv\Scripts\python.exe -m src.backend.worker"
start "MailGraph Worker 2" cmd /k "cd /d %ROOT% && set PYTHONPATH=%ROOT% && %ROOT%\.venv\Scripts\python.exe -m src.backend.worker"

echo [3/3] Starting Frontend (port 5173)...
start "MailGraph Frontend" cmd /k "cd /d %ROOT%\src\web && npm run dev"

echo.
echo ============================================
echo   All services started!
echo.
echo   API:        http://localhost:8000
echo   Frontend:   http://localhost:5173
echo.
echo   To stop:    close the popup windows
echo ============================================
echo.
pause
