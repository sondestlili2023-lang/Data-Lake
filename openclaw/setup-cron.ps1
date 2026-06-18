# Configure les jobs cron OpenClaw pour le projet Vélib (Windows)
# Usage : .\setup-cron.ps1
# Les alertes Telegram passent par POST /telegram/kpi-alert-check (API Docker)

& "$PSScriptRoot\install-crons.ps1"
