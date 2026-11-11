param(
    [string]$PackageRoot = $env:SAN60_PACKAGE_ROOT,
    [ValidateRange(1024,65515)][int]$Port = 8775,
    [ValidateSet('manual','replay')][string]$Mode = 'manual'
)
$ErrorActionPreference = 'Stop'
if (!$PackageRoot) { $PackageRoot = Join-Path $PSScriptRoot '..\..\SAN60\SAN60_Windows_Offline_20261019' }
$PackageRoot = (Resolve-Path -LiteralPath $PackageRoot).Path
$sanPython = Join-Path $PackageRoot 'runtime\python.exe'
$sanLauncher = Join-Path $PackageRoot 'launcher.py'
if (!(Test-Path -LiteralPath $sanPython) -or !(Test-Path -LiteralPath $sanLauncher)) { throw 'SAN60 package is incomplete; check SAN60_PACKAGE_ROOT.' }
$sanProbe = [System.Net.Sockets.TcpClient]::new()
try {
    $sanProbe.Connect('127.0.0.1', $Port)
    throw "Port $Port is occupied. Stop the old SAN60 service or choose another port and set SAN60_SERVICE_URL for the platform backend."
} catch [System.Net.Sockets.SocketException] {
    # The launcher must use the configured port, rather than silently falling back.
} finally { $sanProbe.Dispose() }
$env:PYTHONIOENCODING = 'utf-8'
Write-Host "SAN60: http://127.0.0.1:$Port (mode=$Mode)"
& $sanPython $sanLauncher --check-only
if ($LASTEXITCODE -ne 0) { throw 'SAN60 environment check failed.' }
& $sanPython $sanLauncher --mode $Mode --port $Port --no-browser
exit $LASTEXITCODE
