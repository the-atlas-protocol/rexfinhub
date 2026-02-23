@echo off
REM ============================================================
REM  ETP Filing Tracker - Daily Digest
REM
REM  Schedule via Windows Task Scheduler:
REM    schtasks /create /tn "ETP_Filing_Tracker" /tr "C:\Projects\rexfinhub\run_daily_digest.bat" /sc daily /st 17:00
REM
REM  Or run manually by double-clicking this file.
REM ============================================================

echo === ETP Filing Tracker - Daily Run ===
echo Started: %date% %time%

cd /d C:\Projects\rexfinhub

python run_daily.py

echo.
echo === Finished: %date% %time% ===
pause
