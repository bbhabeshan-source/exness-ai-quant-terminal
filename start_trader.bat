@echo off
title Exness AI Quant Terminal
set "PROJECT_DIR=C:\Users\HP\.gemini\antigravity\scratch\exness_ai_trader"
set "EXNESS_MT5_PATH=C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe"
set "PORT=8899"

echo ============================================================
echo   Exness AI Quant Terminal - Automated Gold ^& Forex Trader
echo ============================================================
echo.

:: 1. Start Exness MetaTrader 5 Terminal if installed and not already running
if exist "%EXNESS_MT5_PATH%" (
    tasklist /FI "IMAGENAME eq terminal64.exe" 2>NUL | find /I "terminal64.exe" >NUL
    if errorlevel 1 (
        echo [1/3] Launching Exness MetaTrader 5 Terminal...
        start "" "%EXNESS_MT5_PATH%"
        timeout /t 4 /nobreak >NUL
    ) else (
        echo [1/3] Exness MetaTrader 5 Terminal already running.
    )
) else (
    echo [1/3] Exness MT5 not found at "%EXNESS_MT5_PATH%" ^(using Paper/Live feed^).
)

:: 2. Start Python trading web server if port 8899 is not already listening
netstat -ano | findstr /R ":%PORT% .*LISTENING" >NUL
if errorlevel 1 (
    echo [2/3] Starting Exness AI Trader server on http://127.0.0.1:%PORT% ...
    cd /d "%PROJECT_DIR%"
    start "Exness AI Trader Server" cmd /k "set EXNESS_MT5_PATH=%EXNESS_MT5_PATH% && set PORT=%PORT% && python server.py"
    timeout /t 4 /nobreak >NUL
) else (
    echo [2/3] Server already running on http://127.0.0.1:%PORT% .
)

:: 3. Open dashboard in default web browser
echo [3/3] Opening http://127.0.0.1:%PORT% in your browser...
start "" "http://127.0.0.1:%PORT%/"
exit /b 0
