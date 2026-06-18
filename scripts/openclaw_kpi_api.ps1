# Déclenche la vérification KPI + envoi Telegram via l'API Docker
$resp = Invoke-RestMethod -Method POST -Uri "http://localhost:8000/alerts/dispatch" -TimeoutSec 60
if ($resp.healthy) { exit 0 }

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
& "$scriptDir\dispatch_whatsapp_alerts.ps1" -Message $resp.message
