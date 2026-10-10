[CmdletBinding()]
param(
  [string]$BaseUrl = 'http://127.0.0.1:8083',
  [Parameter(Mandatory = $true)][PSCredential]$Credential
)
$ErrorActionPreference = 'Stop'
Import-Module Microsoft.PowerShell.Utility
$session = [Microsoft.PowerShell.Commands.WebRequestSession]::new()
$csrf = (Invoke-RestMethod "$BaseUrl/api/auth/csrf" -WebSession $session).data
$headers = @{ $csrf.headerName = $csrf.token }
$login = @{ username = $Credential.UserName; password = $Credential.GetNetworkCredential().Password } | ConvertTo-Json
$null = Invoke-RestMethod "$BaseUrl/api/auth/login" -Method Post -WebSession $session -Headers $headers -ContentType 'application/json' -Body $login
$login = $null
$csrf = (Invoke-RestMethod "$BaseUrl/api/auth/csrf" -WebSession $session).data
$headers = @{ $csrf.headerName = $csrf.token }
$runId = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
$prepared = $false
$passed = 0
try {
  $body = @{ algorithmCode = 'GB_SFLA_CS'; config = @{ standaloneVirtualSimulation = $true; seed = 42 } } | ConvertTo-Json -Depth 5
  $null = Invoke-RestMethod "$BaseUrl/api/algorithm-runs/$runId/prepare" -Method Post -WebSession $session -Headers $headers -ContentType 'application/json' -Body $body
  $prepared = $true
  $runtime = $null
  for ($attempt = 0; $attempt -lt 40; $attempt++) {
    $runtime = (Invoke-RestMethod "$BaseUrl/api/voice/contexts" -WebSession $session).data | Where-Object { $_.algorithmRunId -eq [string]$runId } | Select-Object -First 1
    if ($runtime -and $runtime.capabilities -contains 'DEVICE_COMMAND') { break }
    Start-Sleep -Milliseconds 250
  }
  if (!$runtime) { throw 'Temporary simulation did not become ready.' }
  $corpus = Get-Content -LiteralPath (Join-Path $PSScriptRoot '../docs/voice-control-p1/voice-command-regression.json') -Raw -Encoding UTF8 | ConvertFrom-Json
  foreach ($sample in $corpus) {
    $id = [guid]::NewGuid().ToString()
    $request = @{
      requestId = $id; text = $sample.text; locale = 'zh-CN'; allowedActions = @('START','PAUSE','RESUME','STOP')
      availableDeviceCodes = @('UAV-001','USV-001','USV-002')
      runtimeContext = @{ runtimeRef = $runtime.runtimeRef; runtimeGeneration = $runtime.runtimeGeneration; contextVersion = $runtime.contextVersion }
    } | ConvertTo-Json -Depth 6
    $requestHeaders = @{ $csrf.headerName = $csrf.token; 'X-Request-ID' = $id; 'Idempotency-Key' = $id }
    $parsed = (Invoke-RestMethod "$BaseUrl/api/voice/intelligence/interpretations" -Method Post -WebSession $session -Headers $requestHeaders -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($request))).data
    if ($parsed.status -ne $sample.status) { throw "Status mismatch: $($sample.text), actual $($parsed.status)" }
    if ($sample.actions) {
      $steps = if ($parsed.action -eq 'SEQUENCE') { @($parsed.steps) } else { @($parsed) }
      if ($steps.Count -ne $sample.actions.Count) { throw "Step count mismatch: $($sample.text)" }
      for ($i = 0; $i -lt $steps.Count; $i++) {
        $action = if ($steps[$i].deviceCommandType) { $steps[$i].deviceCommandType } else { $steps[$i].action }
        if ($action -ne $sample.actions[$i] -or [string]$steps[$i].targetDeviceCode -ne [string]$sample.targets[$i]) { throw "Action/target mismatch: $($sample.text), step $i" }
      }
    }
    $passed++
  }
  [pscustomobject]@{ Passed = $passed; Total = $corpus.Count; Mode = 'Text interpretations only; no voice commands dispatched' } | ConvertTo-Json
} finally {
  if ($prepared) { $null = Invoke-RestMethod "$BaseUrl/api/algorithm-runs/$runId/stop" -Method Post -WebSession $session -Headers $headers }
}
