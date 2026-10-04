[CmdletBinding()]
param(
  [string]$ConfigPath = '',
  [int]$BackendPort = 0,
  [int]$FrontendPort = 0,
  [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$repo = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
  $ConfigPath = Join-Path $repo 'config\aliyun-demo.local.psd1'
}
if (!(Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
  throw "Missing local configuration: $ConfigPath`nCopy config/aliyun-demo.example.psd1 to config/aliyun-demo.local.psd1 and fill it first."
}

$config = Import-PowerShellDataFile -LiteralPath $ConfigPath

function Get-ConfigString([string]$Name, [string]$Default = '') {
  if ($config.ContainsKey($Name) -and $null -ne $config[$Name]) {
    return [string]$config[$Name]
  }
  return $Default
}

function Get-Setting([string]$Name, [string]$EnvironmentName, [string]$Default = '') {
  $value = Get-ConfigString $Name
  if (![string]::IsNullOrWhiteSpace($value)) { return $value }
  if (![string]::IsNullOrWhiteSpace($EnvironmentName)) {
    $environmentValue = [Environment]::GetEnvironmentVariable($EnvironmentName, 'Process')
    if (![string]::IsNullOrWhiteSpace($environmentValue)) { return $environmentValue }
  }
  return $Default
}

function Get-ConfigInt([string]$Name, [int]$Default) {
  if (!$config.ContainsKey($Name) -or $null -eq $config[$Name] -or
      [string]::IsNullOrWhiteSpace([string]$config[$Name])) {
    return $Default
  }
  $value = 0
  if (![int]::TryParse([string]$config[$Name], [ref]$value) -or $value -lt 1 -or $value -gt 65535) {
    throw "$Name must be a TCP port between 1 and 65535."
  }
  return $value
}

function Require-Setting([string]$Name, [string]$EnvironmentName) {
  $value = Get-Setting $Name $EnvironmentName
  if ([string]::IsNullOrWhiteSpace($value)) {
    throw "$Name is required in $ConfigPath or the $EnvironmentName process environment variable."
  }
  return $value
}

function Resolve-Executable([string]$Configured, [string[]]$Names) {
  if (![string]::IsNullOrWhiteSpace($Configured)) {
    if (!(Test-Path -LiteralPath $Configured -PathType Leaf)) {
      throw "Configured executable does not exist: $Configured"
    }
    return (Resolve-Path -LiteralPath $Configured).Path
  }
  foreach ($name in $Names) {
    $command = Get-Command $name -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $command) { return $command.Source }
  }
  return $null
}

function New-RandomBase64([int]$Size) {
  $bytes = New-Object byte[] $Size
  $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
  try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
  return [Convert]::ToBase64String($bytes)
}

function New-RandomHex([int]$Size) {
  $bytes = New-Object byte[] $Size
  $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
  try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
  return ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
}

function Test-TcpEndpoint([string]$HostName, [int]$Port, [int]$TimeoutMs = 3000) {
  $client = New-Object Net.Sockets.TcpClient
  try {
    $pending = $client.BeginConnect($HostName, $Port, $null, $null)
    if (!$pending.AsyncWaitHandle.WaitOne($TimeoutMs, $false)) { return $false }
    $client.EndConnect($pending)
    return $true
  } catch {
    return $false
  } finally {
    $client.Dispose()
  }
}

function Set-ScopedEnvironment([hashtable]$Values) {
  $previous = @{}
  foreach ($name in $Values.Keys) {
    $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    [Environment]::SetEnvironmentVariable($name, [string]$Values[$name], 'Process')
  }
  return $previous
}

function Restore-ScopedEnvironment([hashtable]$Previous) {
  foreach ($name in $Previous.Keys) {
    [Environment]::SetEnvironmentVariable($name, $Previous[$name], 'Process')
  }
}

$appKey = Require-Setting 'AliyunAppKey' 'APP_VOICEINTELLIGENCE_ALIYUN_APP_KEY'
$accessKeyId = Require-Setting 'AliyunAccessKeyId' 'ALIYUN_AK_ID'
$accessKeySecret = Require-Setting 'AliyunAccessKeySecret' 'ALIYUN_AK_SECRET'
$mysqlUrl = Require-Setting 'MysqlUrl' 'MYSQL_URL'
$mysqlUsername = Require-Setting 'MysqlUsername' 'MYSQL_USERNAME'
$mysqlPassword = Get-ConfigString 'MysqlPassword'
$environmentMysqlPassword = [Environment]::GetEnvironmentVariable('MYSQL_PASSWORD', 'Process')
if ([string]::IsNullOrEmpty($mysqlPassword) -and $null -ne $environmentMysqlPassword) {
  $mysqlPassword = $environmentMysqlPassword
}
$adminUsername = Require-Setting 'BootstrapAdminUsername' 'BOOTSTRAP_ADMIN_USERNAME'
$adminPassword = Require-Setting 'BootstrapAdminPassword' 'BOOTSTRAP_ADMIN_PASSWORD'
$region = Get-Setting 'AliyunRegion' 'APP_VOICEINTELLIGENCE_ALIYUN_REGION' 'cn-shanghai'
$backendPort = if ($BackendPort -gt 0) { $BackendPort } else { Get-ConfigInt 'BackendPort' 8081 }
$frontendPort = if ($FrontendPort -gt 0) { $FrontendPort } else { Get-ConfigInt 'FrontendPort' 5174 }

if ($backendPort -gt 65535 -or $frontendPort -gt 65535) { throw 'Port overrides must be between 1 and 65535.' }
if ($backendPort -eq $frontendPort) { throw 'BackendPort and FrontendPort must be different.' }
if ($region -notin @('cn-shanghai', 'cn-beijing', 'cn-shenzhen')) {
  throw 'AliyunRegion must be cn-shanghai, cn-beijing, or cn-shenzhen.'
}
if ($accessKeyId -notmatch '^[A-Za-z0-9._-]{8,128}$') {
  throw 'AliyunAccessKeyId has an invalid format.'
}
if ($accessKeySecret.Length -lt 8 -or $accessKeySecret.Length -gt 256) {
  throw 'AliyunAccessKeySecret has an invalid length.'
}
if ($accessKeySecret -match '[^\x21-\x7E]') {
  throw 'AliyunAccessKeySecret must contain visible ASCII characters only.'
}

$java = Resolve-Executable '' @('java.exe', 'java')
$node = Resolve-Executable '' @('node.exe', 'node')
$npm = Resolve-Executable '' @('npm.cmd', 'npm')
$ffmpeg = Resolve-Executable (Get-Setting 'FfmpegPath' 'APP_VOICEINTELLIGENCE_ALIYUN_FFMPEG_PATH') @('ffmpeg.exe', 'ffmpeg')
if ($null -eq $java) { throw 'Java 17 or newer was not found in PATH.' }
if ($null -eq $node) { throw 'Node.js 20.19 or newer was not found in PATH.' }
if ($null -eq $npm) { throw 'npm was not found in PATH.' }
if ($null -eq $ffmpeg) { throw 'FFmpeg was not found. Set FfmpegPath or add ffmpeg to PATH.' }

$javaVersionProcess = New-Object Diagnostics.ProcessStartInfo
$javaVersionProcess.FileName = $java
$javaVersionProcess.Arguments = '-version'
$javaVersionProcess.UseShellExecute = $false
$javaVersionProcess.RedirectStandardOutput = $true
$javaVersionProcess.RedirectStandardError = $true
$javaVersionProcess.CreateNoWindow = $true
$javaVersionRunner = [Diagnostics.Process]::Start($javaVersionProcess)
$javaVersionText = $javaVersionRunner.StandardError.ReadToEnd() +
  $javaVersionRunner.StandardOutput.ReadToEnd()
$javaVersionRunner.WaitForExit()
if ($javaVersionText -notmatch 'version\s+"(?<major>\d+)') { throw 'Could not determine the Java version.' }
$javaMajor = [int]$Matches['major']
if ($javaMajor -eq 1 -and $javaVersionText -match 'version\s+"1\.(?<legacyMajor>\d+)') {
  $javaMajor = [int]$Matches['legacyMajor']
}
if ($javaMajor -lt 17) { throw "Java $javaMajor is too old; Java 17 or newer is required." }

$nodeVersion = (& $node -p 'process.versions.node').Trim()
$nodeParts = $nodeVersion.Split('.')
if ([int]$nodeParts[0] -lt 20 -or ([int]$nodeParts[0] -eq 20 -and [int]$nodeParts[1] -lt 19)) {
  throw "Node.js $nodeVersion is too old; version 20.19 or newer is required."
}

$pythonCandidates = @()
$configuredPython = Get-Setting 'PythonPath' 'APP_ALGORITHM_PYTHON_COMMAND'
if (![string]::IsNullOrWhiteSpace($configuredPython)) { $pythonCandidates += $configuredPython }
$pythonCandidates += (Join-Path $repo 'algorithm-service\.venv\Scripts\python.exe')
$pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -ne $pythonCommand) { $pythonCandidates += $pythonCommand.Source }
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -ne $pythonCommand) { $pythonCandidates += $pythonCommand.Source }
$python = $null
foreach ($candidate in ($pythonCandidates | Select-Object -Unique)) {
  if (!(Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
  & $candidate -c 'import numpy, scipy, sklearn, matplotlib' 2>$null
  if ($LASTEXITCODE -eq 0) { $python = (Resolve-Path -LiteralPath $candidate).Path; break }
}
if ($null -eq $python) {
  throw 'No Python interpreter satisfies algorithm-service/requirements.txt. Set PythonPath after installing those packages.'
}

$mavenWrapper = Join-Path $repo 'backend\mvnw.cmd'
$devScript = Join-Path $repo 'scripts\dev.mjs'
$localExample = Join-Path $repo 'backend\src\main\resources\application-local.example.yml'
$viteEntry = Join-Path $repo 'frontend\node_modules\vite\bin\vite.js'
foreach ($requiredFile in @($mavenWrapper, $devScript, $localExample)) {
  if (!(Test-Path -LiteralPath $requiredFile -PathType Leaf)) { throw "Required project file is missing: $requiredFile" }
}
if (!(Test-Path -LiteralPath $viteEntry -PathType Leaf)) {
  throw 'Frontend dependencies are missing. Run: npm --prefix frontend install'
}

if ($mysqlUrl -notmatch '^jdbc:mysql://(?<host>\[[^\]]+\]|[^/:?]+)(:(?<port>\d+))?/(?<database>[^?]+)') {
  throw 'MysqlUrl must be a jdbc:mysql://host:port/database URL.'
}
$mysqlHost = $Matches['host'].Trim('[', ']')
$mysqlPort = 3306
if (![string]::IsNullOrWhiteSpace($Matches['port'])) { $mysqlPort = [int]$Matches['port'] }
if (!(Test-TcpEndpoint $mysqlHost $mysqlPort)) {
  throw "MySQL is not reachable at ${mysqlHost}:${mysqlPort}. Start it and create the configured database first."
}

$occupied = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
  Where-Object { $_.LocalPort -in @($backendPort, $frontendPort) }
if ($occupied) {
  $details = ($occupied | ForEach-Object { "$($_.LocalPort) (PID $($_.OwningProcess))" } | Sort-Object -Unique) -join ', '
  throw "Required port is already in use: $details"
}

$resultEncryptionKey = Get-ConfigString 'ResultEncryptionKey'
if ([string]::IsNullOrWhiteSpace($resultEncryptionKey)) {
  $resultEncryptionKey = New-RandomBase64 32
  Write-Warning 'ResultEncryptionKey is blank; encrypted ASR outcomes from this run cannot be recovered after restart. Configure a stable local key for repeatable recovery.'
} else {
  try { $keyBytes = [Convert]::FromBase64String($resultEncryptionKey) } catch {
    throw 'ResultEncryptionKey must be valid Base64.'
  }
  if ($keyBytes.Length -ne 32) { throw 'ResultEncryptionKey must decode to exactly 32 bytes.' }
}

Write-Host 'Aliyun demo prerequisite check passed.' -ForegroundColor Green
Write-Host "  Java:    $javaMajor ($java)"
Write-Host "  Node:    $nodeVersion"
Write-Host "  Python:  $python"
Write-Host "  FFmpeg:  $ffmpeg"
Write-Host "  MySQL:   ${mysqlHost}:${mysqlPort}"
Write-Host "  Ports:   backend $backendPort, frontend $frontendPort"
Write-Host '  Secrets: configured (values hidden)'

if ($CheckOnly) { return }

$environment = @{
  'SERVER_PORT' = [string]$backendPort
  'MYSQL_URL' = $mysqlUrl
  'MYSQL_USERNAME' = $mysqlUsername
  'MYSQL_PASSWORD' = $mysqlPassword
  'SPRING_DATASOURCE_URL' = $mysqlUrl
  'SPRING_DATASOURCE_USERNAME' = $mysqlUsername
  'SPRING_DATASOURCE_PASSWORD' = $mysqlPassword
  'BOOTSTRAP_ADMIN_USERNAME' = $adminUsername
  'BOOTSTRAP_ADMIN_PASSWORD' = $adminPassword
  'APP_VOICEINTELLIGENCE_ENABLED' = 'true'
  'APP_VOICEINTELLIGENCE_PROVIDER' = 'aliyun'
  'APP_VOICEINTELLIGENCE_INTENT_PROVIDER' = 'rules'
  'APP_VOICEINTELLIGENCE_ALIYUN_APP_KEY' = $appKey
  'APP_VOICEINTELLIGENCE_ALIYUN_TOKEN' = ''
  'ALIYUN_AK_ID' = $accessKeyId
  'ALIYUN_AK_SECRET' = $accessKeySecret
  'APP_VOICEINTELLIGENCE_ALIYUN_REGION' = $region
  'APP_VOICEINTELLIGENCE_ALIYUN_FFMPEG_PATH' = $ffmpeg
  'APP_VOICEINTELLIGENCE_ALIYUN_MODEL_ALIAS' = 'aliyun-shiyinshi-v1'
  'APP_VOICEINTELLIGENCE_RESULT_ENCRYPTION_KEY' = $resultEncryptionKey
  'PLATFORM_INTEGRATION_TOKEN' = (New-RandomHex 32)
  'VOICE_CONTROL_ENABLED' = 'true'
  'ROS_GATEWAY_V1_ENABLED' = 'false'
  'VISUAL_SENSOR_WEBSOCKET_ENABLED' = 'false'
  'ROS_ENABLED' = 'false'
  'COMMAND_DISPATCH_MODE' = 'browser-unity'
  'APP_ALGORITHM_PYTHON_COMMAND' = $python
  'APP_ALGORITHM_RUNNER_PATH' = (Join-Path $repo 'algorithm-service\runner.py')
  'SPRING_CONFIG_ADDITIONAL_LOCATION' = ('optional:file:' + $localExample.Replace('\', '/'))
  'VITE_DEV_PORT' = [string]$frontendPort
  'VITE_BACKEND_TARGET' = "http://127.0.0.1:$backendPort"
  'VITE_VOICE_P0_MOCK' = 'false'
  'VITE_VOICE_P1_PREPARATION' = 'true'
  'VITE_VOICE_P1_BACKEND' = 'true'
  'VITE_VOICE_P1_MOCK' = 'false'
  'VITE_VOICE_ASR_ONLY' = 'false'
  'VITE_VOICE_UNITY_PRESENTATION_V1' = 'true'
}

$previous = Set-ScopedEnvironment $environment
try {
  Write-Host "Starting full voice-control demo at http://127.0.0.1:$frontendPort/?workspace=simulation" -ForegroundColor Cyan
  Write-Host 'Press Ctrl+C to stop the foreground development services.'
  & $node $devScript
  if ($LASTEXITCODE -ne 0) { throw "Development services exited with code $LASTEXITCODE." }
} finally {
  Restore-ScopedEnvironment $previous
}
