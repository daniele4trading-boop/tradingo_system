<#
.SYNOPSIS
    Migra segreti da tradingo_config.json / webapp_config.json / variabili Machine
    all'archivio DPAPI (C:\TG_TradinGo\secrets\store.dpapi).

.DESCRIPTION
    1. Backup di tradingo_config.json e webapp_config.json.
    2. Legge i segreti dai file JSON e dalle variabili d'ambiente Machine.
    3. Li scrive nell'archivio DPAPI tramite secrets_store CLI.
    4. Sostituisce i valori nei JSON con il sentinel "@secrets".
    5. Applica ACL restrittivi sulla cartella secrets/ (solo Administrator).
    6. Rimuove le variabili d'ambiente Machine SOLO dopo che export-check passa.

.PARAMETER WhatIf
    Mostra le azioni senza eseguirle.

.EXAMPLE
    .\migrate_secrets_to_store.ps1 -WhatIf
    .\migrate_secrets_to_store.ps1
#>

[CmdletBinding(SupportsShouldProcess)]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$TG_ROOT    = "C:\TG_TradinGo"
$CFG_JSON   = Join-Path $TG_ROOT "tradingo_config.json"
$WCFG_JSON  = Join-Path $TG_ROOT "webapp_config.json"
$SECRETS_DIR = Join-Path $TG_ROOT "secrets"
$PYTHON     = "python"

function Write-Step { param($msg) Write-Host ">> $msg" -ForegroundColor Cyan }
function Write-Ok   { param($msg) Write-Host "   OK: $msg" -ForegroundColor Green }
function Write-Skip { param($msg) Write-Host "   SKIP: $msg" -ForegroundColor Yellow }

# ── 1. Backup ────────────────────────────────────────────────────────────────
Write-Step "Backup configurazioni"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
foreach ($f in @($CFG_JSON, $WCFG_JSON)) {
    if (Test-Path $f) {
        $bk = "$f.bak_$stamp"
        if ($PSCmdlet.ShouldProcess($f, "Backup -> $bk")) {
            Copy-Item $f $bk
            Write-Ok "$f -> $bk"
        }
    } else {
        Write-Skip "$f non trovato"
    }
}

# ── 2. Leggi segreti dai JSON ─────────────────────────────────────────────────
Write-Step "Lettura credenziali Telegram da tradingo_config.json"
$tgApiId   = $null
$tgApiHash = $null
if (Test-Path $CFG_JSON) {
    $cfg = Get-Content $CFG_JSON -Raw | ConvertFrom-Json
    $tgApiId   = $cfg.telegram.api_id
    $tgApiHash = $cfg.telegram.api_hash
    Write-Ok "TELEGRAM_API_ID=$tgApiId"
    Write-Ok "TELEGRAM_API_HASH=***"
}

Write-Step "Lettura WEBAPP_SECRET_KEY da webapp_config.json"
$webSecretKey = $null
if (Test-Path $WCFG_JSON) {
    $wcfg = Get-Content $WCFG_JSON -Raw | ConvertFrom-Json
    $webSecretKey = $wcfg.secret_key
    Write-Ok "WEBAPP_SECRET_KEY=***"
}

# ── 3. Scrivi nell'archivio DPAPI ─────────────────────────────────────────────
Write-Step "Scrittura segreti nell'archivio DPAPI"

function Set-Secret {
    param([string]$Name, [string]$Value)
    if (-not $Value -or $Value -eq "@secrets") {
        Write-Skip "$Name: valore vuoto o gia' sentinel, skip"
        return
    }
    if ($PSCmdlet.ShouldProcess($Name, "set nell'archivio DPAPI")) {
        $Value | & $PYTHON -m tg_tradingo.secrets_store set $Name
        Write-Ok "$Name salvato"
    }
}

if ($tgApiId)     { Set-Secret "TELEGRAM_API_ID"   "$tgApiId" }
if ($tgApiHash)   { Set-Secret "TELEGRAM_API_HASH"  "$tgApiHash" }
if ($webSecretKey){ Set-Secret "WEBAPP_SECRET_KEY"  "$webSecretKey" }

# Variabili d'ambiente Machine
$machineKeys = @(
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_API_KEY_BACKUP",
    "GEMINI_API_KEY",
    "TELEGRAM_AGENT_BOT_TOKEN",
    "DXTRADE_PASSWORD"
)
foreach ($k in $machineKeys) {
    $v = [System.Environment]::GetEnvironmentVariable($k, "Machine")
    if ($v) {
        Set-Secret $k $v
    } else {
        Write-Skip "$k: variabile Machine assente"
    }
}

# ── 4. export-check ───────────────────────────────────────────────────────────
Write-Step "Verifica archivio (export-check)"
$checkOk = $false
if ($PSCmdlet.ShouldProcess("export-check", "Esegui")) {
    & $PYTHON -m tg_tradingo.secrets_store export-check
    $checkOk = ($LASTEXITCODE -eq 0)
}

if (-not $checkOk) {
    Write-Host "export-check FALLITO: i JSON e le variabili Machine NON verranno modificati." -ForegroundColor Red
    exit 1
}

# ── 5. ACL sulla cartella secrets/ ────────────────────────────────────────────
Write-Step "Applicazione ACL restrittivi su $SECRETS_DIR"
if ($PSCmdlet.ShouldProcess($SECRETS_DIR, "icacls: solo Administrator")) {
    if (-not (Test-Path $SECRETS_DIR)) {
        New-Item -ItemType Directory -Path $SECRETS_DIR | Out-Null
    }
    icacls $SECRETS_DIR /reset | Out-Null
    icacls $SECRETS_DIR /inheritance:r | Out-Null
    icacls $SECRETS_DIR /grant "Administrator:(OI)(CI)F" | Out-Null
    Write-Ok "ACL applicati"
}

# ── 6. Sostituisci valori nei JSON con @secrets ───────────────────────────────
Write-Step "Sostituzione sentinel @secrets nei file di configurazione"

if ((Test-Path $CFG_JSON) -and $tgApiId -and $tgApiHash) {
    if ($PSCmdlet.ShouldProcess($CFG_JSON, "Sostituisci api_id/api_hash con @secrets")) {
        $raw = Get-Content $CFG_JSON -Raw
        $raw = $raw -replace '"api_id"\s*:\s*[^,}\r\n]+', '"api_id": "@secrets"'
        $raw = $raw -replace '"api_hash"\s*:\s*"[^"]*"', '"api_hash": "@secrets"'
        Set-Content $CFG_JSON $raw -Encoding UTF8
        Write-Ok "$CFG_JSON aggiornato"
    }
}

if ((Test-Path $WCFG_JSON) -and $webSecretKey -and $webSecretKey -ne "@secrets") {
    if ($PSCmdlet.ShouldProcess($WCFG_JSON, "Sostituisci secret_key con @secrets")) {
        $raw = Get-Content $WCFG_JSON -Raw
        $raw = $raw -replace '"secret_key"\s*:\s*"[^"]*"', '"secret_key": "@secrets"'
        Set-Content $WCFG_JSON $raw -Encoding UTF8
        Write-Ok "$WCFG_JSON aggiornato"
    }
}

# ── 7. Rimuovi variabili d'ambiente Machine ───────────────────────────────────
Write-Step "Rimozione variabili d'ambiente Machine"
foreach ($k in $machineKeys) {
    $v = [System.Environment]::GetEnvironmentVariable($k, "Machine")
    if ($v) {
        if ($PSCmdlet.ShouldProcess($k, "Rimuovi da variabili Machine")) {
            [System.Environment]::SetEnvironmentVariable($k, $null, "Machine")
            Write-Ok "$k rimosso da Machine"
        }
    } else {
        Write-Skip "$k: gia' assente"
    }
}

Write-Host ""
Write-Host "Migrazione completata. Testare il bridge prima di riavviarlo." -ForegroundColor Green
