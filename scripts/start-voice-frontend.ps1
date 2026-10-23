[CmdletBinding()]
param(
  [ValidateRange(1, 65535)][int]$FrontendPort = 5177,
  [ValidateRange(1, 65535)][int]$BackendPort = 8083,
  [switch]$CheckOnly
)

# Foreground frontend-only entry point. Does not stop processes or restart the backend.
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$frontend = Join-Path $repo 'frontend'
$vite = Join-Path $frontend 'node_modules/vite/bin/vite.js'
$node = (Get-Command node.exe -ErrorAction Stop).Source
if (!(Test-Path -LiteralPath $vite -PathType Leaf)) { throw 'Run npm install in frontend first.' }
if ($FrontendPort -eq $BackendPort) { throw 'Frontend and backend ports must differ.' }
$listener = Get-NetTCPConnection -LocalPort $FrontendPort -State Listen -ErrorAction SilentlyContinue
if ($listener) { throw "Port $FrontendPort is already occupied; no process was stopped." }
$health = Invoke-WebRequest -Uri "http://127.0.0.1:$BackendPort/api/auth/csrf" -TimeoutSec 5 -UseBasicParsing
if ($health.StatusCode -ne 200) { throw 'Backend is not ready.' }
$vitePackage = Get-Content (Join-Path $frontend 'node_modules/vite/package.json') -Raw | ConvertFrom-Json
Write-Host "Frontend prerequisites ready: Vite $($vitePackage.version), backend $BackendPort."
if ($CheckOnly) { return }
$values = @{
  VITE_DEV_PORT = [string]$FrontendPort
  VITE_BACKEND_TARGET = "http://127.0.0.1:$BackendPort"
  VITE_VOICE_P1_PREPARATION = 'true'
  VITE_VOICE_P1_BACKEND = 'true'
  VITE_VOICE_P1_MOCK = 'false'
  VITE_VOICE_P0_MOCK = 'false'
  VITE_VOICE_UNITY_PRESENTATION_V1 = 'true'
  VITE_VOICE_ASR_ONLY = 'false'
}
$previous = @{}
foreach ($name in $values.Keys) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
Push-Location $frontend
try {
  foreach ($name in $values.Keys) { [Environment]::SetEnvironmentVariable($name, $values[$name], 'Process') }
  Write-Host "Open http://localhost:$FrontendPort/?workspace=simulation ; keep this terminal open."
  & $node $vite --host 127.0.0.1 --port $FrontendPort --strictPort
  if ($LASTEXITCODE -ne 0) { throw "Frontend exited with code $LASTEXITCODE." }
} finally {
  foreach ($name in $previous.Keys) { [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process') }
  Pop-Location
}
