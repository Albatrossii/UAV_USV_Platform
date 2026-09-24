[CmdletBinding()]
param(
    [string]$ToolRoot = 'C:/Users/hp-pc/Desktop/Project4/Project/UAV_USV_Platform/.local-tools/d3-llm',
    [switch]$Start
)
$ErrorActionPreference = 'Stop'
$server = Join-Path $ToolRoot 'llama-b11147/llama-server.exe'
$model = Join-Path $ToolRoot 'models/qwen2.5-1.5b-instruct-q4_k_m.gguf'
$credentials = Join-Path $ToolRoot 'credentials.json'
$keyFile = Join-Path $ToolRoot 'api-key.txt'
$expectedModelHash = '6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e'
foreach($file in @($server,$model,$credentials)){if(!(Test-Path -LiteralPath $file -PathType Leaf)){throw "Missing local file: $file"}}
$actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $model).Hash.ToLowerInvariant()
if($actualHash -ne $expectedModelHash){throw "Model SHA256 mismatch: $actualHash"}
$secret = Get-Content -Raw -LiteralPath $credentials | ConvertFrom-Json
if([string]::IsNullOrWhiteSpace($secret.token) -or $secret.token.Length -lt 32){throw 'Invalid local LLM credential'}
Write-Output 'D3 LLM checked: llama.cpp b11147; Qwen2.5-1.5B-Instruct Q4_K_M; 127.0.0.1:18083; model SHA256 verified.'
if(!$Start){Write-Output 'CHECK ONLY. Add -Start to run in the foreground.';return}
if(Get-NetTCPConnection -State Listen -LocalPort 18083 -ErrorAction SilentlyContinue){throw 'Port 18083 occupied. Verify and stop only the existing D3 LLM process.'}
Set-Content -LiteralPath $keyFile -Value $secret.token -Encoding ascii -NoNewline
& $server '--model' $model '--host' '127.0.0.1' '--port' '18083' '--threads' '4' '--threads-batch' '4' '--ctx-size' '2048' '--parallel' '1' '--api-key-file' $keyFile '--no-webui'
if($LASTEXITCODE -ne 0){throw "llama-server exited with code $LASTEXITCODE"}
