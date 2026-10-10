param(
  [string]$PythonCommand = 'python',
  [Parameter(Mandatory = $true)][string]$FfmpegPath,
  [int]$MysqlPort = 3307,
  [PSCredential]$MysqlCredential,
  [string]$TestPattern = '*Voice*,*Intent*,*Asr*,*Aliyun*'
)

# These suites own random test databases and child processes, never the live scene.
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if ($MysqlPort -lt 1 -or $MysqlPort -gt 65535) { throw 'Invalid MySQL port.' }
$python = (Get-Command $PythonCommand -ErrorAction Stop).Source
$ffmpeg = (Resolve-Path -LiteralPath $FfmpegPath -ErrorAction Stop).Path
$names = @('P0_REAL_RUNNER', 'PYTHON_COMMAND', 'TEST_FFMPEG_PATH',
  'VOICE_TEST_MYSQL_URL', 'VOICE_TEST_MYSQL_USER', 'VOICE_TEST_MYSQL_PASSWORD')
$previous = @{}
foreach ($name in $names) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
$started = Get-Date
Push-Location (Join-Path $repo 'backend')
try {
  $env:P0_REAL_RUNNER = Join-Path $repo 'algorithm-service/runner.py'
  $env:PYTHON_COMMAND = $python
  $env:TEST_FFMPEG_PATH = $ffmpeg
  # Root URL only: each suite creates and drops its own UUID-named test database.
  $env:VOICE_TEST_MYSQL_URL = "jdbc:mysql://127.0.0.1:$MysqlPort/?useUnicode=true&characterEncoding=utf8&serverTimezone=Asia/Shanghai&useSSL=false&allowPublicKeyRetrieval=true"
  $env:VOICE_TEST_MYSQL_USER = if ($MysqlCredential) { $MysqlCredential.UserName } else { 'root' }
  $env:VOICE_TEST_MYSQL_PASSWORD = if ($MysqlCredential) { $MysqlCredential.GetNetworkCredential().Password } else { '' }
  & ./mvnw.cmd -q "-Dtest=$TestPattern" test
  if ($LASTEXITCODE -ne 0) { throw "Integration tests failed (exit $LASTEXITCODE). See backend/target/surefire-reports." }
  $totals = @{ Tests = 0; Failures = 0; Errors = 0; Skipped = 0; Suites = 0 }
  Get-ChildItem target/surefire-reports/TEST-*.xml |
    Where-Object { $_.LastWriteTime -ge $started } |
    ForEach-Object {
      [xml]$report = Get-Content -LiteralPath $_.FullName -Raw
      $totals.Suites++
      foreach ($key in @('Tests', 'Failures', 'Errors', 'Skipped')) {
        $totals[$key] += [int]$report.testsuite.GetAttribute($key.ToLowerInvariant())
      }
    }
  $totals | ConvertTo-Json -Compress | Write-Output
  if ($totals.Tests -eq 0 -or $totals.Skipped -ne 0) { throw 'Acceptance incomplete: no tests or skipped tests remain.' }
} finally {
  foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process') }
  Pop-Location
}
