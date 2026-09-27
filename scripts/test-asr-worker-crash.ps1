param(
    [Parameter(Mandatory = $true)][string]$Python,
    [Parameter(Mandatory = $true)][string]$ModelPath,
    [Parameter(Mandatory = $true)][string]$AudioPath,
    [Parameter(Mandatory = $true)][string]$OutputPath,
    [int]$Port = 18084
)

$ErrorActionPreference = 'Stop'
$server = Resolve-Path (Join-Path $PSScriptRoot '..\asr-service\asr_server.py')
$pythonPath = (Resolve-Path $Python).Path
$model = (Resolve-Path $ModelPath).Path
$audio = (Resolve-Path $AudioPath).Path
$token = ([Guid]::NewGuid().ToString('N') + [Guid]::NewGuid().ToString('N'))
$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ('uav-usv-asr-crash-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tempRoot | Out-Null
$stdout = Join-Path $tempRoot 'stdout.log'
$stderr = Join-Path $tempRoot 'stderr.log'
$launcher = $null
$requestJob = $null

function Test-HttpOk([string]$Url) {
    try {
        $response = Invoke-WebRequest -Uri $Url -TimeoutSec 2 -UseBasicParsing
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

try {
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $Port is already in use"
    }

    $oldToken = $env:ASR_SERVICE_TOKEN
    $oldModel = $env:ASR_MODEL_PATH
    $oldThreads = $env:ASR_CPU_THREADS
    $oldPort = $env:ASR_PORT
    $oldOffline = $env:HF_HUB_OFFLINE
    try {
        $env:ASR_SERVICE_TOKEN = $token
        $env:ASR_MODEL_PATH = $model
        $env:ASR_CPU_THREADS = '4'
        $env:ASR_PORT = [string]$Port
        $env:HF_HUB_OFFLINE = '1'
        $launcher = Start-Process -FilePath $pythonPath -ArgumentList @($server.Path) -RedirectStandardOutput $stdout -RedirectStandardError $stderr -WindowStyle Hidden -PassThru
    } finally {
        $env:ASR_SERVICE_TOKEN = $oldToken
        $env:ASR_MODEL_PATH = $oldModel
        $env:ASR_CPU_THREADS = $oldThreads
        $env:ASR_PORT = $oldPort
        $env:HF_HUB_OFFLINE = $oldOffline
    }

    $ready = $false
    for ($i = 0; $i -lt 240; $i++) {
        if (Test-HttpOk "http://127.0.0.1:$Port/health/ready") {
            $ready = $true
            break
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) { throw 'Disposable ASR did not become ready' }

    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen | Select-Object -First 1
    $workerPid = [int]$listener.OwningProcess
    $worker = Get-CimInstance Win32_Process -Filter "ProcessId = $workerPid"
    if (-not $worker.CommandLine.Contains($server.Path) -or $worker.CommandLine.Contains('runner.py')) {
        throw 'Listener identity check failed; refusing to terminate it'
    }

    $runnerBefore = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*algorithm-service*runner.py*' }).ProcessId
    $primaryReadyBefore = Test-HttpOk 'http://127.0.0.1:18082/health/ready'
    $requestId = [Guid]::NewGuid().ToString().ToLowerInvariant()
    $requestJob = Start-Job -ScriptBlock {
        param($TargetPort, $Bearer, $Id, $SourceAudio)
        $client = $null
        $content = $null
        $stream = $null
        try {
            $client = [Net.Http.HttpClient]::new()
            $client.Timeout = [TimeSpan]::FromSeconds(130)
            $client.DefaultRequestHeaders.Authorization = [Net.Http.Headers.AuthenticationHeaderValue]::new('Bearer', $Bearer)
            $client.DefaultRequestHeaders.Add('X-ASR-Timeout-Ms', '120000')
            $content = [Net.Http.MultipartFormDataContent]::new()
            $content.Add([Net.Http.StringContent]::new($Id), 'requestId')
            $content.Add([Net.Http.StringContent]::new('zh-CN'), 'locale')
            $stream = [IO.File]::OpenRead($SourceAudio)
            $audioContent = [Net.Http.StreamContent]::new($stream)
            $mime = if ([IO.Path]::GetExtension($SourceAudio) -ieq '.webm') { 'audio/webm' } else { 'audio/mpeg' }
            $audioContent.Headers.ContentType = [Net.Http.Headers.MediaTypeHeaderValue]::new($mime)
            $content.Add($audioContent, 'audio', [IO.Path]::GetFileName($SourceAudio))
            $response = $client.PostAsync("http://127.0.0.1:$TargetPort/internal/asr/transcriptions", $content).GetAwaiter().GetResult()
            [pscustomobject]@{ completed = $true; status = [int]$response.StatusCode; failureType = $null }
        } catch {
            $status = $null
            if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
            [pscustomobject]@{ completed = $false; status = $status; failureType = $_.Exception.GetType().Name }
        } finally {
            if ($content) { $content.Dispose() }
            if ($stream) { $stream.Dispose() }
            if ($client) { $client.Dispose() }
        }
    } -ArgumentList $Port, $token, $requestId, $audio

    $connectionObserved = $false
    for ($i = 0; $i -lt 100; $i++) {
        if (Get-NetTCPConnection -LocalPort $Port -State Established -ErrorAction SilentlyContinue) {
            $connectionObserved = $true
            break
        }
        if ($requestJob.State -ne 'Running') { break }
        Start-Sleep -Milliseconds 100
    }
    if (-not $connectionObserved) { throw 'The request never established a connection to the disposable ASR worker' }
    Start-Sleep -Seconds 2
    $workerSample = Get-Process -Id $workerPid
    $workerWorkingSet = [long]$workerSample.WorkingSet64
    $workerCpu = [double]$workerSample.CPU
    $runnerAliveAtCrash = @($runnerBefore | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue }).Count -eq $runnerBefore.Count
    Stop-Process -Id $workerPid -Force
    Wait-Process -Id $workerPid -Timeout 15 -ErrorAction SilentlyContinue
    $rawResult = Receive-Job -Job $requestJob -Wait -AutoRemoveJob
    $result = [ordered]@{
        completed = [bool]$rawResult.completed
        status = $rawResult.status
        failureType = [string]$rawResult.failureType
    }
    $requestJob = $null
    $runnerAfter = @($runnerBefore | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
    $primaryReadyAfter = Test-HttpOk 'http://127.0.0.1:18082/health/ready'

    $evidence = [ordered]@{
        testedAt = [DateTimeOffset]::Now.ToString('o')
        scenario = 'E1 I08 disposable real ASR worker crash during inference'
        disposablePort = $Port
        readyBeforeCrash = $ready
        requestId = $requestId
        connectionObservedBeforeCrash = $connectionObserved
        requestResult = $result
        worker = [ordered]@{
            pid = $workerPid
            workingSetBytesBeforeCrash = $workerWorkingSet
            cpuSecondsBeforeCrash = $workerCpu
            exitedAfterCrash = -not [bool](Get-Process -Id $workerPid -ErrorAction SilentlyContinue)
        }
        algorithmRunner = [ordered]@{
            pidsBefore = $runnerBefore
            aliveAtCrash = $runnerAliveAtCrash
            pidsAliveAfter = $runnerAfter
            unaffected = ($runnerBefore.Count -gt 0 -and $runnerAfter.Count -eq $runnerBefore.Count)
        }
        primaryAsr = [ordered]@{
            readyBefore = $primaryReadyBefore
            readyAfter = $primaryReadyAfter
            unaffected = ($primaryReadyBefore -and $primaryReadyAfter)
        }
        safeguards = @(
            'Disposable port and random in-memory token used',
            'Listener command line matched asr_server.py before termination',
            'algorithm-service/runner.py process was never targeted',
            'No audio, transcript, token, cookie, or password stored'
        )
    }
    $output = [IO.Path]::GetFullPath((Join-Path (Get-Location) $OutputPath))
    New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($output)) -Force | Out-Null
    $evidence | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $output -Encoding utf8
    if (-not $evidence.connectionObservedBeforeCrash -or -not $evidence.worker.exitedAfterCrash -or -not $evidence.algorithmRunner.unaffected -or -not $evidence.primaryAsr.unaffected -or $result.completed) {
        throw 'Crash isolation expectations were not all satisfied'
    }
    Write-Output $output
} finally {
    if ($requestJob) { Stop-Job $requestJob -ErrorAction SilentlyContinue; Remove-Job $requestJob -Force -ErrorAction SilentlyContinue }
    if ($launcher -and -not $launcher.HasExited) { Stop-Process -Id $launcher.Id -Force -ErrorAction SilentlyContinue }
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
