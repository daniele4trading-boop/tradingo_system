param(
    [Parameter(Mandatory = $true)]
    [string]$TerminalDataPath,

    [string]$EaName = "TradinGo_ICT_QualityFailed_XAUUSD"
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$sourceExpertDir = Join-Path $repoRoot "mql5\Experts\TradinGo"
$sourcePresetDir = Join-Path $repoRoot "mql5\Presets"

$targetExpertDir = Join-Path $TerminalDataPath "MQL5\Experts\TradinGo"
$targetTesterDir = Join-Path $TerminalDataPath "MQL5\Profiles\Tester"

New-Item -ItemType Directory -Path $targetExpertDir -Force | Out-Null
New-Item -ItemType Directory -Path $targetTesterDir -Force | Out-Null

Copy-Item -Path (Join-Path $sourceExpertDir "$EaName.mq5") -Destination $targetExpertDir -Force
Copy-Item -Path (Join-Path $sourceExpertDir "$EaName.ex5") -Destination $targetExpertDir -Force
Copy-Item -Path (Join-Path $sourcePresetDir "${EaName}_spread25.set") -Destination $targetTesterDir -Force

Write-Output "Installed EA to: $targetExpertDir"
Write-Output "Installed preset to: $targetTesterDir"
