# Finalise OpenClaw + Telegram — à lancer après avoir envoyé /start au bot
param(
    [Parameter(Mandatory = $true)]
    [string]$ChatId,
    [string]$NgrokAuthtoken = ""
)

$ErrorActionPreference = "Stop"
$openclawJson = "$env:USERPROFILE\.openclaw\openclaw.json"
$repo = "c:\Users\secre\Desktop\code\projet ecole\Data-Lake"

Write-Host "=== 1. Chat ID : $ChatId ==="

$envFile = Join-Path $repo ".env"
$cfg = Get-Content $openclawJson -Raw | ConvertFrom-Json
$token = $cfg.channels.telegram.botToken
$existing = @{}
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^([^#=]+)=(.*)$') { $existing[$Matches[1].Trim()] = $Matches[2].Trim() }
    }
}
$ingestScope = if ($existing['INGEST_SCOPE']) { $existing['INGEST_SCOPE'] } else { 'paris' }
$gbfsBase = if ($existing['VELIB_GBFS_BASE']) { $existing['VELIB_GBFS_BASE'] } else { 'https://velib-metropole-opendata.smovengo.cloud/opendata/Velib_Metropole' }
$enableJcd = if ($existing['ENABLE_JCDECAUX']) { $existing['ENABLE_JCDECAUX'] } else { 'false' }
$jcdKey = if ($existing['JCDECAUX_API_KEY']) { $existing['JCDECAUX_API_KEY'] } else { '' }
$lines = @(
    "TELEGRAM_BOT_TOKEN=$token",
    "TELEGRAM_CHAT_ID=$ChatId",
    "INGEST_SCOPE=$ingestScope",
    "VELIB_GBFS_BASE=$gbfsBase",
    "ENABLE_JCDECAUX=$enableJcd",
    "JCDECAUX_API_KEY=$jcdKey"
)
$lines | Set-Content $envFile -Encoding UTF8
Write-Host "Fichier .env mis a jour (Docker)"

Write-Host "=== 2. Skill velib-analytics ==="
$skillSrc = Join-Path $repo "openclaw\skills\velib-analytics"
$skillDst = "$env:USERPROFILE\.openclaw\workspace\skills\velib-analytics"
New-Item -ItemType Directory -Force -Path $skillDst | Out-Null
Copy-Item "$skillSrc\SKILL.md" "$skillDst\SKILL.md" -Force

Write-Host "=== 3. Crons (PowerShell direct, sans curl) ==="
& "$repo\openclaw\install-crons.ps1"

Write-Host "=== 4. ngrok (optionnel soutenance) ==="
if ($NgrokAuthtoken) { ngrok config add-authtoken $NgrokAuthtoken }
Write-Host "ngrok http 18789 + webhookUrl dans openclaw.json si besoin"

Write-Host "=== 5. Redemarrer ==="
Write-Host "- docker compose up -d api"
Write-Host "- Relancer gateway.cmd"
Write-Host "- Tester sur Telegram : 'vélib' ou 'stations critiques'"

openclaw pairing list telegram
