# Recrée les crons OpenClaw (appelle install-crons.js pour JSON correct sous Windows)
$ErrorActionPreference = "Stop"
$js = Join-Path $PSScriptRoot "install-crons.js"
node $js
