# Déclenche l'ingestion Vélib (cron OpenClaw / Windows)
Invoke-RestMethod -Method POST -Uri "http://localhost:8000/ingest" -TimeoutSec 120 | Out-Null
