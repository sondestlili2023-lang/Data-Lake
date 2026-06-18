# Appelle l'API KPI et affiche une alerte ou NO_REPLY (pour cron OpenClaw)
$status = Invoke-RestMethod -Uri "http://localhost:8000/analytics/kpi-status" -TimeoutSec 30
if ($status.healthy) {
    Write-Output "NO_REPLY"
    exit 0
}
$lines = @("🚨 Alerte Vélib — KPI hors seuil")
foreach ($a in $status.alerts) {
    $icon = if ($a.severity -eq "critical") { "🔴" } else { "🟠" }
    $lines += "$icon $($a.city) — $($a.message)"
}
Write-Output ($lines -join "`n")
