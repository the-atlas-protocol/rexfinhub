@echo off
echo Setting up ETP Watcher scheduled task (every 30 minutes)...
schtasks /create /tn "ETP_Watcher" /tr "python C:\Projects\rexfinhub\scripts\run_watcher.py" /sc minute /mo 30 /f
if %errorlevel%==0 (
    echo SUCCESS: ETP_Watcher scheduled every 30 minutes
    schtasks /query /tn "ETP_Watcher"
) else (
    echo FAILED: Run this script as Administrator
)
pause
