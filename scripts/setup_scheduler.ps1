# setup_scheduler.ps1 - Create Windows Task Scheduler entries for REX pipelines
#
# Run this script ONCE as Administrator:
#   powershell -ExecutionPolicy Bypass -File C:\Foundry\Rexfinhub\scripts\setup_scheduler.ps1
#
# Creates scheduled tasks:
#   REX_SEC_0800  - 8:00 AM weekdays  (SEC pipeline + DB upload, no market, no email)
#   REX_SEC_1200  - 12:00 PM weekdays (SEC pipeline + DB upload, no market, no email)
#   REX_SEC_1600  - 4:00 PM weekdays  (SEC pipeline + DB upload, no market, no email)
#   REX_SEC_2000  - 8:00 PM weekdays  (SEC pipeline + DB upload, no market, no email)
#
# All tasks:
#   - Wake the PC from sleep (WakeToRun)
#   - Run even on battery power
#   - Run if the PC was off at trigger time (StartWhenAvailable)
#   - Auto-kill after 1 hour (safety timeout)

$ErrorActionPreference = "Stop"

# Use the full path -- the WindowsApps "python" alias doesn't work from Task Scheduler
$PythonExe = "C:\Users\RyuEl-Asmar\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\python.exe"
$Script = "C:\Foundry\Rexfinhub\scripts\run_all_pipelines.py"
$WorkingDir = "C:\Foundry\Rexfinhub"

# Verify the script exists
if (-not (Test-Path $Script)) {
    Write-Error "Script not found: $Script"
    exit 1
}

# --- Remove legacy tasks if they exist ---
foreach ($old in @("REX_Morning_Pipeline", "REX_Evening_Pipeline",
                    "REX_Scrape_0800", "REX_Scrape_1200", "REX_Scrape_2100",
                    "REX_Email_1700")) {
    $existing = Get-ScheduledTask -TaskName $old -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $old -Confirm:$false
        Write-Host "  Removed legacy task: $old" -ForegroundColor Yellow
    }
}

# --- Helper function ---
function New-PipelineTask {
    param(
        [string]$TaskName,
        [string]$TriggerTime,
        [string]$Description,
        [string]$ExtraArgs = ""
    )

    Write-Host "`nCreating task: $TaskName ($TriggerTime weekdays)" -ForegroundColor Cyan

    # Remove existing task if present
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "  Removed existing task."
    }

    # Action: run python with the script + optional extra args
    $argument = $Script
    if ($ExtraArgs) {
        $argument = "$Script $ExtraArgs"
    }

    $action = New-ScheduledTaskAction `
        -Execute $PythonExe `
        -Argument $argument `
        -WorkingDirectory $WorkingDir

    # Trigger: weekdays at specified time
    $trigger = New-ScheduledTaskTrigger `
        -Weekly `
        -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
        -At $TriggerTime

    # Settings
    $settings = New-ScheduledTaskSettingsSet `
        -WakeToRun `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
        -MultipleInstances IgnoreNew

    # Register the task (runs as current user, no password needed for non-elevated)
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Description $Description

    Write-Host "  Created: $TaskName" -ForegroundColor Green
}

# --- Create SEC scrape tasks (every 4 hours from 8 AM, weekdays) ---

New-PipelineTask `
    -TaskName "REX_SEC_0800" `
    -TriggerTime "8:00AM" `
    -ExtraArgs "--skip-email --skip-market" `
    -Description "REX SEC Scrape - 8 AM. SEC pipeline + DB upload. No market, no email."

New-PipelineTask `
    -TaskName "REX_SEC_1200" `
    -TriggerTime "12:00PM" `
    -ExtraArgs "--skip-email --skip-market" `
    -Description "REX SEC Scrape - 12 PM. SEC pipeline + DB upload. No market, no email."

New-PipelineTask `
    -TaskName "REX_SEC_1600" `
    -TriggerTime "4:00PM" `
    -ExtraArgs "--skip-email --skip-market" `
    -Description "REX SEC Scrape - 4 PM. SEC pipeline + DB upload. No market, no email."

New-PipelineTask `
    -TaskName "REX_SEC_2000" `
    -TriggerTime "8:00PM" `
    -ExtraArgs "--skip-email --skip-market" `
    -Description "REX SEC Scrape - 8 PM. SEC pipeline + DB upload. No market, no email."

# --- Verify ---
Write-Host "`n--- Verification ---" -ForegroundColor Yellow
Get-ScheduledTask -TaskName "REX_*" | Format-Table TaskName, State, @{
    Label = "NextRunTime"
    Expression = { (Get-ScheduledTaskInfo -TaskName $_.TaskName).NextRunTime }
}

Write-Host "Done. All tasks will wake the PC from sleep to run." -ForegroundColor Green
Write-Host ""
Write-Host "Schedule (weekdays only, SEC scrape only):"
Write-Host "  8:00 AM  - SEC scrape + DB upload to Render"
Write-Host "  12:00 PM - SEC scrape + DB upload to Render"
Write-Host "  4:00 PM  - SEC scrape + DB upload to Render"
Write-Host "  8:00 PM  - SEC scrape + DB upload to Render"
Write-Host ""
Write-Host "Bloomberg market data (run bbg) is NOT scheduled -- run manually."
Write-Host "Emails are NOT scheduled -- send manually via 'send daily' / 'send weekly'."
Write-Host ""
Write-Host "To run manually:"
Write-Host "  Start-ScheduledTask -TaskName 'REX_SEC_0800'"
Write-Host ""
Write-Host "To remove all:"
Write-Host "  Get-ScheduledTask -TaskName 'REX_*' | Unregister-ScheduledTask -Confirm:`$false"
