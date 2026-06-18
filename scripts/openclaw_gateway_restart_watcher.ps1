# Surveille .gateway-restart-requested dans ~/.openclaw et redémarre le gateway.
param(
    [string]$ConfigDir = "$env:USERPROFILE\.openclaw"
)

$trigger = Join-Path $ConfigDir ".gateway-restart-requested"
$done = Join-Path $ConfigDir ".gateway-restart-done"

Write-Host "[velib] Watcher redémarrage OpenClaw actif ($ConfigDir)"

while ($true) {
    if (Test-Path $trigger) {
        Remove-Item $trigger -Force -ErrorAction SilentlyContinue
        Write-Host "[velib] Redémarrage gateway OpenClaw..."
        $ok = $false
        $errorMsg = ""
        try {
            $output = openclaw gateway restart 2>&1
            if ($LASTEXITCODE -eq 0) {
                $ok = $true
            } else {
                $errorMsg = ($output | Out-String).Trim()
                if (-not $errorMsg) { $errorMsg = "openclaw gateway restart exit $LASTEXITCODE" }
            }
        } catch {
            $errorMsg = $_.Exception.Message
        }
        @{ ok = $ok; error = $errorMsg } | ConvertTo-Json | Set-Content $done -Encoding UTF8
        if ($ok) {
            Write-Host "[velib] Gateway redémarré."
        } else {
            Write-Warning "[velib] Échec redémarrage: $errorMsg"
        }
    }
    Start-Sleep -Seconds 1
}
