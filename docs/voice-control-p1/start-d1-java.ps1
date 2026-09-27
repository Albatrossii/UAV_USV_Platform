[CmdletBinding()]
param([switch]$Start,[string]$Java='C:/Program Files/Java/jdk-21/bin/java.exe')
$ErrorActionPreference='Stop'
$repo=(Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$jar=Join-Path $repo 'backend/target/platform-backend-0.1.0-SNAPSHOT.jar'
$p0=([Uri](Join-Path $repo 'docs/voice-control-p0/integration/application-p0-integration.yml')).AbsoluteUri
$d1=([Uri](Join-Path $PSScriptRoot 'application-d1-asr.yml')).AbsoluteUri
if(!(Test-Path -LiteralPath $jar)){throw 'Build backend package first'}
Write-Output 'P1 Java: isolated database, port 18081; ASR=127.0.0.1:18082; optional D3 LLM=127.0.0.1:18083; no model downloads.'
if(!$Start){Write-Output 'CHECK ONLY. Set credentials in process environment, then use -Start.';return}
foreach($name in @('P0_DB_PASSWORD','P0_ADMIN_PASSWORD','P0_INTEGRATION_TOKEN','P0_PYTHON','P0_RUNNER','D1_ASR_TOKEN','D1_ASR_MODEL_REVISION')){if([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name,'Process'))){throw "Missing environment variable: $name"}}
if($env:D3_INTENT_PROVIDER -eq 'local-llm'){
    foreach($name in @('D3_LLM_TOKEN','D3_LLM_MODEL')){
        if([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name,'Process'))){throw "Missing environment variable: $name"}
    }
}
if(Get-NetTCPConnection -State Listen -LocalPort 18081 -ErrorAction SilentlyContinue){throw 'Port 18081 occupied. Coordinate stop of identified service first.'}
$conflicts=@(Get-ChildItem Env: | Where-Object {$_.Name -match '^(SPRING_|APP_|SERVER_|MANAGEMENT_)|^(JAVA_TOOL_OPTIONS|JDK_JAVA_OPTIONS|_JAVA_OPTIONS)$'})
if($conflicts.Count){throw 'Conflicting inherited configuration. Use a clean terminal.'}
$old=$env:D1_ASR_ENABLED
try{$env:D1_ASR_ENABLED='true'; & $Java '-jar' $jar "--spring.config.location=$p0,$d1" '--spring.profiles.active=p0-integration';if($LASTEXITCODE -ne 0){throw 'Backend exited unsuccessfully'}}finally{$env:D1_ASR_ENABLED=$old}
