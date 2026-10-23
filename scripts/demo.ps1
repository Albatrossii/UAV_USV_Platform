[CmdletBinding(SupportsShouldProcess = $true)]
param(
  [ValidateSet('Start', 'Check', 'Status', 'Stop')][string]$Action = 'Status',
  [string]$ConfigPath = '',
  [int]$BackendPort = 0,
  [int]$FrontendPort = 0,
  [int]$MysqlPort = 0
)
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if ($Action -eq 'Stop') {
  $controlPath = Join-Path $repo '.local-tools/demo-control.json'
  if (!(Test-Path -LiteralPath $controlPath -PathType Leaf)) {
    throw 'No managed demo is running. Existing manually started services were NOT stopped; use their original terminal/launcher.'
  }
  $control = Get-Content -LiteralPath $controlPath -Raw | ConvertFrom-Json
  if ([IO.Path]::GetFullPath([string]$control.root) -ne $repo -or
      $control.port -lt 1 -or $control.port -gt 65535 -or
      [string]$control.token -notmatch '^[a-f0-9]{64}$') { throw 'Invalid demo ownership record; refusing to stop.' }
  if ($PSCmdlet.ShouldProcess($repo, 'Stop this launcher-owned frontend/backend (MySQL is preserved)')) {
    $null = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$($control.port)/stop" -Headers @{ 'X-Demo-Token' = $control.token } -TimeoutSec 5
    Write-Output 'Shutdown accepted by the owning launcher. Check its terminal for completion; no device commands are resent.'
  }
  return
}
if ($Action -in @('Start', 'Check')) {
  $options = @{}
  if ($ConfigPath) { $options.ConfigPath = $ConfigPath }
  if ($BackendPort) { $options.BackendPort = $BackendPort }
  if ($FrontendPort) { $options.FrontendPort = $FrontendPort }
  if ($Action -eq 'Check') { $options.CheckOnly = $true }
  if ($Action -eq 'Check' -or $PSCmdlet.ShouldProcess($repo, 'Start configured local voice demo')) {
    & (Join-Path $PSScriptRoot 'start-aliyun-demo.ps1') @options
  }
  return
}
if (!$ConfigPath) { $ConfigPath = Join-Path $repo 'config/aliyun-demo.local.psd1' }
if (Test-Path -LiteralPath $ConfigPath -PathType Leaf) {
  $settings = Import-PowerShellDataFile -LiteralPath $ConfigPath
  if (!$BackendPort) { $BackendPort = if ($settings.BackendPort) { [int]$settings.BackendPort } else { 8081 } }
  if (!$FrontendPort) { $FrontendPort = if ($settings.FrontendPort) { [int]$settings.FrontendPort } else { 5174 } }
  if (!$MysqlPort -and $settings.MysqlUrl -match '^jdbc:mysql://(?:\[[^\]]+\]|[^/:?]+)(?::(\d+))?/') {
    $MysqlPort = if ($Matches[1]) { [int]$Matches[1] } else { 3306 }
  }
}
if (!$BackendPort) { $BackendPort = 8083 }
if (!$FrontendPort) { $FrontendPort = 5177 }
if (!$MysqlPort) { $MysqlPort = 3307 }
& (Join-Path $PSScriptRoot 'check-demo-health.ps1') -BackendPort $BackendPort -FrontendPort $FrontendPort -MysqlPort $MysqlPort
