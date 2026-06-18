# Lance le watcher de redémarrage auto + le gateway OpenClaw (à utiliser à la place de gateway.cmd seul).
$repoRoot = Split-Path -Parent $PSScriptRoot
$watcher = Join-Path $repoRoot "scripts\openclaw_gateway_restart_watcher.ps1"
$configDir = "$env:USERPROFILE\.openclaw"

if (-not (Test-Path $configDir)) {
    Write-Error "Dossier OpenClaw introuvable: $configDir"
    exit 1
}

# Évite les doublons si le watcher tourne déjà
$existing = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*openclaw_gateway_restart_watcher.ps1*" }
if (-not $existing) {
    Start-Process powershell -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", "`"$watcher`"", "-ConfigDir", "`"$configDir`""
    ) -WindowStyle Hidden
    Write-Host "[velib] Watcher redémarrage auto démarré."
} else {
    Write-Host "[velib] Watcher déjà actif."
}

$gatewayCmd = Join-Path $configDir "gateway.cmd"
if (Test-Path $gatewayCmd) {
    Write-Host "[velib] Démarrage gateway via gateway.cmd..."
    & $gatewayCmd
} else {
    Write-Host "[velib] gateway.cmd absent - openclaw gateway run..."
    openclaw gateway run
}
