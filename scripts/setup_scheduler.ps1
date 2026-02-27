# setup_scheduler.ps1 - Create Windows Task Scheduler entries for REX pipelines
#
# Run this script ONCE as Administrator:
#   powershell -ExecutionPolicy Bypass -File C:\Projects\rexfinhub\scripts\setup_scheduler.ps1
#
# Creates four scheduled tasks:
#   REX_Scrape_0800  - 8:00 AM weekdays  (SEC + market pipeline + DB upload, no email)
#   REX_Scrape_1200  - 12:00 PM weekdays (SEC + market pipeline + DB upload, no email)
#   REX_Scrape_2100  - 9:00 PM weekdays  (SEC + market pipeline + DB upload, no email)
#   REX_Email_1700   - 5:00 PM weekdays  (email dispatch only: daily brief + weekly on Mon)
#
# All tasks:
#   - Wake the PC from sleep (WakeToRun)
#   - Run even on battery power
#   - Run if the PC was off at trigger time (StartWhenAvailable)
#   - Auto-kill after 1 hour (safety timeout)

$ErrorActionPreference = "Stop"

# Use the full path -- the WindowsApps "python" alias doesn't work from Task Scheduler
$PythonExe = "C:\Users\RyuEl-Asmar\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\python.exe"
$Script = "C:\Projects\rexfinhub\scripts\run_all_pipelines.py"
$WorkingDir = "C:\Projects\rexfinhub"

# Verify the script exists
if (-not (Test-Path $Script)) {
    Write-Error "Script not found: $Script"
    exit 1
}

# --- Remove legacy tasks if they exist ---
foreach ($old in @("REX_Morning_Pipeline", "REX_Evening_Pipeline")) {
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

# --- Create all four tasks ---

New-PipelineTask `
    -TaskName "REX_Scrape_0800" `
    -TriggerTime "8:00AM" `
    -ExtraArgs "--skip-email" `
    -Description "REX ETP Tracker - 8 AM scrape. SEC + market pipeline + DB upload. No email."

New-PipelineTask `
    -TaskName "REX_Scrape_1200" `
    -TriggerTime "12:00PM" `
    -ExtraArgs "--skip-email" `
    -Description "REX ETP Tracker - 12 PM scrape. SEC + market pipeline + DB upload. No email."

New-PipelineTask `
    -TaskName "REX_Scrape_2100" `
    -TriggerTime "9:00PM" `
    -ExtraArgs "--skip-email" `
    -Description "REX ETP Tracker - 9 PM scrape. SEC + market pipeline + DB upload. No email."

New-PipelineTask `
    -TaskName "REX_Email_1700" `
    -TriggerTime "5:00PM" `
    -ExtraArgs "--email-only" `
    -Description "REX ETP Tracker - 5 PM email dispatch. Daily brief (+ weekly on Mondays)."

# --- Verify ---
Write-Host "`n--- Verification ---" -ForegroundColor Yellow
Get-ScheduledTask -TaskName "REX_*" | Format-Table TaskName, State, @{
    Label = "NextRunTime"
    Expression = { (Get-ScheduledTaskInfo -TaskName $_.TaskName).NextRunTime }
}

Write-Host "Done. All tasks will wake the PC from sleep to run." -ForegroundColor Green
Write-Host ""
Write-Host "Schedule:"
Write-Host "  8:00 AM  - Scrape (SEC + market + upload)"
Write-Host "  12:00 PM - Scrape (SEC + market + upload)"
Write-Host "  5:00 PM  - Email dispatch (daily brief + weekly on Mon)"
Write-Host "  9:00 PM  - Scrape (SEC + market + upload)"
Write-Host ""
Write-Host "To run manually:"
Write-Host "  Start-ScheduledTask -TaskName 'REX_Scrape_0800'"
Write-Host "  Start-ScheduledTask -TaskName 'REX_Email_1700'"
Write-Host ""
Write-Host "To remove all:"
Write-Host "  Get-ScheduledTask -TaskName 'REX_*' | Unregister-ScheduledTask -Confirm:`$false"
