<#
.SYNOPSIS
    Esegue un comando con le chiavi dell'archivio DPAPI come variabili d'ambiente.

.DESCRIPTION
    Legge tutte le chiavi dall'archivio DPAPI e le esporta come variabili
    d'ambiente per la durata del comando specificato. Utile per Claude Code,
    Gemini CLI e qualsiasi strumento che legge le API key dall'ambiente.

    Le variabili vengono impostate SOLO nel processo figlio: l'ambiente
    del terminale corrente non viene modificato.

.PARAMETER Command
    Comando da eseguire (con argomenti opzionali).

.EXAMPLE
    # Claude Code
    .\with_secrets.ps1 claude --model claude-sonnet-5 "analizza il bridge"

    # Gemini CLI
    .\with_secrets.ps1 gemini "analizza il bridge"

    # Script Python arbitrario
    .\with_secrets.ps1 python mio_script.py

.NOTES
    apiKeyHelper per Claude Code / Gemini CLI — dopo la migrazione dalle
    variabili Machine:

    Claude Code (PowerShell):
        $env:ANTHROPIC_API_KEY = (python -m tg_tradingo.secrets_store get ANTHROPIC_API_KEY --reveal)
        claude ...

    Gemini CLI (PowerShell):
        $env:GEMINI_API_KEY = (python -m tg_tradingo.secrets_store get GEMINI_API_KEY --reveal)
        gemini ...

    Oppure usare questo script come wrapper:
        .\with_secrets.ps1 claude "..."
        .\with_secrets.ps1 gemini "..."
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0, ValueFromRemainingArguments)]
    [string[]]$Command
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$PYTHON = "python"

# Leggi le chiavi disponibili nell'archivio
$keys = & $PYTHON -m tg_tradingo.secrets_store list 2>$null
if ($LASTEXITCODE -ne 0 -or -not $keys) {
    Write-Warning "Archivio segreti vuoto o non inizializzato. Avviare il comando senza env injection."
    & $Command[0] @($Command | Select-Object -Skip 1)
    exit $LASTEXITCODE
}

# Costruisci l'env per il processo figlio
$envOverride = @{}
foreach ($k in $keys) {
    $k = $k.Trim()
    if (-not $k) { continue }
    $val = & $PYTHON -m tg_tradingo.secrets_store get $k --reveal 2>$null
    if ($val) {
        $envOverride[$k] = $val
    }
}

# Esegui il comando con le variabili iniettate nell'ambiente del processo figlio
$exe  = $Command[0]
$args = $Command | Select-Object -Skip 1

$psi = [System.Diagnostics.ProcessStartInfo]::new()
$psi.FileName = $exe
$psi.UseShellExecute = $false
$psi.RedirectStandardInput = $false
# Copia l'ambiente corrente
foreach ($kv in [System.Environment]::GetEnvironmentVariables().GetEnumerator()) {
    $psi.Environment[$kv.Key] = $kv.Value
}
# Sovrascrive con i segreti
foreach ($kv in $envOverride.GetEnumerator()) {
    $psi.Environment[$kv.Key] = $kv.Value
}
if ($args) {
    $psi.Arguments = ($args | ForEach-Object { """$_""" }) -join " "
}

$proc = [System.Diagnostics.Process]::Start($psi)
$proc.WaitForExit()
exit $proc.ExitCode
