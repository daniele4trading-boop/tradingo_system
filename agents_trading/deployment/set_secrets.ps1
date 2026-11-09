#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Setup encrypted credentials using DPAPI for secure storage.

.DESCRIPTION
    Encrypts and stores MT5 password + API key using Windows DPAPI.
    Credentials are user-specific and machine-specific (cannot be moved).

.PARAMETER ApiKey
    Anthropic API key (sk-...)

.PARAMETER Account
    Exness account number (e.g., 480578231)

.PARAMETER Password
    MT5 terminal password for the account

.EXAMPLE
    .\set_secrets.ps1 -ApiKey "sk-xxx" -Account 480578231 -Password "your-password"
#>

param(
    [Parameter(Mandatory=$true)]
    [string]$ApiKey,

    [Parameter(Mandatory=$true)]
    [int]$Account,

    [Parameter(Mandatory=$true)]
    [string]$Password
)

Write-Host "🔐 Setting up encrypted credentials..." -ForegroundColor Yellow

# Create credentials directory
$CredDir = "$env:APPDATA\agents_trading"
if (-not (Test-Path $CredDir)) {
    New-Item -ItemType Directory -Path $CredDir -Force | Out-Null
    Write-Host "✅ Created credentials directory: $CredDir"
}

# Create secrets object
$Secrets = @{
    api_key = $ApiKey
    account = $Account
    password = $Password
    created_at = (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
    machine = $env:COMPUTERNAME
    user = $env:USERNAME
} | ConvertTo-Json

# Encrypt and save
$SecretsFile = Join-Path $CredDir "secrets.json"
$SecretBytes = [System.Text.Encoding]::UTF8.GetBytes($Secrets)
$EncryptedBytes = [System.Security.Cryptography.ProtectedData]::Protect(
    $SecretBytes,
    $null,
    [System.Security.Cryptography.DataProtectionScope]::CurrentUser
)

# Save encrypted file
[System.IO.File]::WriteAllBytes($SecretsFile, $EncryptedBytes)

# Set restrictive permissions (owner only)
$Acl = Get-Acl $SecretsFile
$Acl.SetAccessRuleProtection($true, $false)
$Rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
    $env:USERNAME,
    "FullControl",
    "Allow"
)
$Acl.SetAccessRule($Rule)
Set-Acl -Path $SecretsFile -AclObject $Acl

Write-Host "✅ Credentials encrypted and saved to: $SecretsFile" -ForegroundColor Green
Write-Host "✅ Permissions set to user-only" -ForegroundColor Green
Write-Host ""
Write-Host "Stored credentials:" -ForegroundColor Cyan
Write-Host "  Account: $Account"
Write-Host "  API Key: $($ApiKey.Substring(0,10))..."
Write-Host "  Machine: $env:COMPUTERNAME"
Write-Host "  User: $env:USERNAME"
Write-Host ""
Write-Host "📌 To use in Python:" -ForegroundColor Cyan
Write-Host '  from secrets_store import load_secrets'
Write-Host '  secrets = load_secrets()'
Write-Host '  api_key = secrets["api_key"]'
Write-Host '  password = secrets["password"]'
