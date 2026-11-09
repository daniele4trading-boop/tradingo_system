#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Monitor live trading P&L and status.

.DESCRIPTION
    Tail the trading log in real-time and show key metrics.
    Updates every 5 seconds.

.EXAMPLE
    .\monitor_trading.ps1

.EXAMPLE
    .\monitor_trading.ps1 -Lines 100
#>

param(
    [int]$Lines = 50,
    [int]$RefreshSeconds = 5
)

$LogFile = "C:\StatArb\logs\agents_trading.log"

if (-not (Test-Path $LogFile)) {
    Write-Host "❌ Log file not found: $LogFile" -ForegroundColor Red
    Write-Host "Has trading been started? Check: Get-Process python"
    exit 1
}

Write-Host "🔄 Monitoring trading... (Ctrl+C to stop)" -ForegroundColor Green
Write-Host "Log: $LogFile" -ForegroundColor Cyan
Write-Host ""

$LastSize = 0

while ($true) {
    Clear-Host

    Write-Host "📊 TRADING MONITOR - $(Get-Date -Format 'HH:mm:ss')" -ForegroundColor Cyan
    Write-Host "=" * 80

    # Get current log size
    $CurrentSize = (Get-Item $LogFile).Length

    # Show last N lines
    $Content = Get-Content $LogFile -Tail $Lines -ErrorAction SilentlyContinue

    if ($Content) {
        # Parse log for key metrics
        $LatestLines = @($Content)[-10..0] | Where-Object { $_ }

        foreach ($Line in $LatestLines) {
            # Color code different types of messages
            if ($Line -match "Signal: BUY|Signal: SELL") {
                Write-Host $Line -ForegroundColor Green
            }
            elseif ($Line -match "Position closed|TP_HIT") {
                Write-Host $Line -ForegroundColor Cyan
            }
            elseif ($Line -match "SL_HIT|Risk check failed") {
                Write-Host $Line -ForegroundColor Yellow
            }
            elseif ($Line -match "❌|Error|failed") {
                Write-Host $Line -ForegroundColor Red
            }
            else {
                Write-Host $Line -ForegroundColor White
            }
        }
    }

    Write-Host ""
    Write-Host "=" * 80

    # Check if process running
    $PythonProcess = Get-Process python -ErrorAction SilentlyContinue
    if ($PythonProcess) {
        Write-Host "✅ Python process running" -ForegroundColor Green
        Write-Host "Memory: $([Math]::Round($PythonProcess.WorkingSet / 1MB, 1)) MB"
    }
    else {
        Write-Host "⚠️  Python process NOT running" -ForegroundColor Yellow
        Write-Host "Start trading: cd C:\StatArb\tradingo_system\agents_trading && python main.py ..."
    }

    Write-Host ""
    Write-Host "Refreshing in $RefreshSeconds seconds... (Ctrl+C to stop)" -ForegroundColor Gray

    Start-Sleep -Seconds $RefreshSeconds
}
