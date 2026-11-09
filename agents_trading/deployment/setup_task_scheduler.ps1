#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Setup Windows Task Scheduler job for 24/7 automated trading.

.DESCRIPTION
    Creates a scheduled task that runs daily at 06:00 UTC and executes
    the trading orchestrator for 1440 minutes (24 hours) with auto-restart.

.EXAMPLE
    .\setup_task_scheduler.ps1
#>

Write-Host "⚙️  Setting up Task Scheduler job..." -ForegroundColor Yellow

# Verify running as Administrator
$IsAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")
if (-not $IsAdmin) {
    Write-Host "❌ This script must run as Administrator" -ForegroundColor Red
    Write-Host "Run PowerShell as Administrator and try again"
    exit 1
}

$WorkDir = "C:\StatArb\tradingo_system\agents_trading"
$LogDir = "C:\StatArb\logs"

# Create log directory
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    Write-Host "✅ Created log directory: $LogDir"
}

# Task name
$TaskName = "AgentsTrading"
$TaskPath = "\ExnessTrading\"

# Remove existing task if present
try {
    Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
    Write-Host "⚠️  Removed existing task: $TaskName"
}
catch {
    # Task doesn't exist, that's OK
}

# Create task principal (run as SYSTEM with highest privileges)
$Principal = New-ScheduledTaskPrincipal -UserID "NT AUTHORITY\SYSTEM" -LogonType ServiceAccount -RunLevel Highest

# Create trigger: Daily at 06:00 UTC
$Trigger = New-ScheduledTaskTrigger -Daily -At 06:00

# Create action: Run Python orchestrator
$Action = New-ScheduledTaskAction `
    -Execute "python.exe" `
    -Argument "main.py --duration 1440 --symbols XAUUSD,EURUSD,GBPUSD" `
    -WorkingDirectory $WorkDir

# Configure task settings
$Settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -WakeToRun `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 25)

# Create and register task
try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Principal $Principal `
        -Trigger $Trigger `
        -Action $Action `
        -Settings $Settings `
        -Force `
        -ErrorAction Stop | Out-Null

    Write-Host "✅ Created task: $TaskName" -ForegroundColor Green
}
catch {
    Write-Host "❌ Failed to create task: $_" -ForegroundColor Red
    exit 1
}

# Get task info
$Task = Get-ScheduledTask -TaskName $TaskName
Write-Host ""
Write-Host "📋 Task Details:" -ForegroundColor Cyan
Write-Host "  Task Name: $TaskName"
Write-Host "  Schedule: Daily at 06:00 UTC"
Write-Host "  Duration: 1440 minutes (24 hours)"
Write-Host "  Working Dir: $WorkDir"
Write-Host "  Run As: SYSTEM"
Write-Host "  Status: $($Task.State)"
Write-Host ""

# Suggest additional setup
Write-Host "📌 Next Steps:" -ForegroundColor Yellow
Write-Host "  1. Setup credentials: .\set_secrets.ps1 -ApiKey 'sk-...' -Account 480578231 -Password 'pwd'"
Write-Host "  2. Test manually: cd $WorkDir && python main.py --duration 30 --symbols XAUUSD --dry-run"
Write-Host "  3. Monitor logs: Get-Content C:\StatArb\logs\agents_trading.log -Tail 50 -Wait"
Write-Host "  4. Task will start tomorrow at 06:00 UTC"
Write-Host ""

# Manual test option
$Response = Read-Host "Run manual test now? (y/n)"
if ($Response -eq "y") {
    Write-Host ""
    Write-Host "🟢 Running manual test (30 min, dry-run mode)..." -ForegroundColor Green
    Push-Location $WorkDir
    try {
        python main.py --duration 30 --symbols XAUUSD --dry-run
    }
    finally {
        Pop-Location
    }
}
