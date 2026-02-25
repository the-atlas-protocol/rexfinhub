@echo off
echo ==========================================
echo  REX Dev Server - Live Reload
echo ==========================================
echo.
cd /d C:\Projects\rexfinhub

:: Start Caddy (named URLs)
:: Try PATH first, then winget install location
set "CADDY=caddy"
where caddy >nul 2>&1
if %ERRORLEVEL% neq 0 (
    for /f "delims=" %%i in ('dir /s /b "%LOCALAPPDATA%\Microsoft\WinGet\Packages\*caddy.exe" 2^>nul') do set "CADDY=%%i"
)
if exist "%USERPROFILE%\.caddy\Caddyfile" (
    echo Starting Caddy...
    "%CADDY%" stop --config "%USERPROFILE%\.caddy\Caddyfile" >nul 2>&1
    "%CADDY%" start --config "%USERPROFILE%\.caddy\Caddyfile" >nul 2>&1
    if %ERRORLEVEL% equ 0 (
        echo   Caddy OK - http://rexfinhub.local ready
    ) else (
        echo   Caddy failed - using localhost fallback
    )
)

:: Start uvicorn in background
echo Starting uvicorn on :8000...
start /B python -m uvicorn webapp.main:app --reload --port 8000

:: Wait for uvicorn
echo Waiting for uvicorn...
timeout /t 4 /nobreak >nul

:: Start browser-sync
echo Starting browser-sync on :3000...
echo.
echo ==========================================
echo   App:          http://rexfinhub.local
echo   Fallback:     http://localhost:3000
echo   BrowserSync:  http://localhost:3001
echo ==========================================
echo   Press Ctrl+C to stop
echo.
browser-sync start --proxy "localhost:8000" --port 3000 --files "webapp/templates/**/*.html" --files "webapp/static/**/*.css" --files "webapp/static/**/*.js" --no-notify
