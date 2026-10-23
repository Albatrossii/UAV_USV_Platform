[CmdletBinding()]
param([int]$BackendPort = 8083, [int]$FrontendPort = 5177, [int]$MysqlPort = 3307)
$ErrorActionPreference = 'Stop'
# Read-only: never logs in, dispatches commands, restarts services or prints secrets.
function Probe-Http([string]$Url) {
  try {
    $response = Invoke-WebRequest -Uri $Url -TimeoutSec 5 -UseBasicParsing
    return [pscustomobject]@{ Status = [int]$response.StatusCode; Ready = $response.StatusCode -eq 200 }
  } catch { return [pscustomobject]@{ Status = 0; Ready = $false } }
}
$backend = Probe-Http "http://127.0.0.1:$BackendPort/api/auth/csrf"
$frontend = Probe-Http "http://127.0.0.1:$FrontendPort/"
$proxy = Probe-Http "http://127.0.0.1:$FrontendPort/api/auth/csrf"
$mysql = [Net.Sockets.TcpClient]::new()
try {
  $pending = $mysql.BeginConnect('127.0.0.1', $MysqlPort, $null, $null)
  $mysqlReady = $pending.AsyncWaitHandle.WaitOne(2000, $false)
  if ($mysqlReady) { $mysql.EndConnect($pending) }
} catch { $mysqlReady = $false } finally { $mysql.Dispose() }
[pscustomobject]@{
  Backend = $backend.Ready; Frontend = $frontend.Ready; FrontendProxy = $proxy.Ready; MysqlTcp = $mysqlReady
  Notes = 'HTTP/TCP checks only. Aliyun transcription needs a signed-in audio test; Unity needs a loaded scene. No commands were sent.'
} | ConvertTo-Json
if (!$backend.Ready -or !$frontend.Ready -or !$proxy.Ready -or !$mysqlReady) { exit 1 }
