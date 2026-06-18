param(
    [string]$Message = "",
    [string]$ApiBase = "http://localhost:8000"
)

$ErrorActionPreference = "Continue"
$failed = 0
$sent = 0

if (-not $Message) {
    $dispatch = Invoke-RestMethod -Method POST -Uri "$ApiBase/alerts/dispatch" -TimeoutSec 60
    if ($dispatch.healthy) { exit 0 }
    $Message = $dispatch.message
}

if (-not $Message) {
    Write-Error "Message alerte vide — abandon WhatsApp."
    exit 1
}

$recipients = Invoke-RestMethod -Uri "$ApiBase/alerts/recipients?for_dispatch=true" -TimeoutSec 30
$phones = @($recipients.items | ForEach-Object { $_.phone_e164 })
if (-not $phones.Count) {
    Write-Host "Aucun destinataire WhatsApp actif."
    exit 0
}

foreach ($phone in $phones) {
    Write-Host "Envoi WhatsApp -> $phone"
    $output = openclaw message send --channel whatsapp -t $phone -m $Message 2>&1
    if ($LASTEXITCODE -ne 0) {
        $failed++
        Write-Warning ($output | Out-String).Trim()
    } else {
        $sent++
        Write-Host "OK -> $phone"
    }
}

if ($failed -gt 0 -and $sent -eq 0) {
    Write-Error @"
Échec envoi WhatsApp pour tous les destinataires.
Causes fréquentes :
  1. Gateway OpenClaw arrêté → lancez openclaw\start-gateway-with-watcher.ps1
  2. WhatsApp non connecté → openclaw channels login --channel whatsapp --account default (scan QR)
  3. Numéro absent de allowFrom → ajoutez-le sur http://localhost:8000/alerts puis redémarrez le gateway
"@
    exit 1
}

exit 0
