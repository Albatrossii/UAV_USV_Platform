[CmdletBinding()]
param(
  [string]$BaseUrl = 'http://127.0.0.1:8083',
  [Parameter(Mandatory = $true)][PSCredential]$Credential,
  [string]$RuntimeRef = '',
  [string]$ReportPath = ''
)

# Uses an already-prepared command-edition scene. Never prepares/replaces a run,
# creates a proposal, confirms a command, or calls a paid ASR provider.
$ErrorActionPreference = 'Stop'
$session = [Microsoft.PowerShell.Commands.WebRequestSession]::new()
$csrf = (Invoke-RestMethod "$BaseUrl/api/auth/csrf" -WebSession $session).data
$login = @{ username = $Credential.UserName; password = $Credential.GetNetworkCredential().Password } | ConvertTo-Json
try {
  $null = Invoke-RestMethod "$BaseUrl/api/auth/login" -Method Post -WebSession $session -Headers @{ $csrf.headerName = $csrf.token } -ContentType 'application/json' -Body $login
} finally { $login = $null }
$csrf = (Invoke-RestMethod "$BaseUrl/api/auth/csrf" -WebSession $session).data
$contexts = @((Invoke-RestMethod "$BaseUrl/api/voice/contexts" -WebSession $session).data | Where-Object {
  $_.capabilities -contains 'ALGORITHM_PRESET' -and $_.state -in @('PREPARED','PREVIEW','RUNNING','PAUSED') -and (!$RuntimeRef -or $_.runtimeRef -eq $RuntimeRef)
})
if ($contexts.Count -ne 1) { throw 'Select exactly one prepared command-edition scene, or supply -RuntimeRef.' }
$runtime = $contexts[0]
$catalog = (Invoke-RestMethod "$BaseUrl/api/voice/algorithm-presets" -WebSession $session).data
if ($catalog.version -ne 'escort-20261020' -or @($catalog.items).Count -ne 21) { throw 'Unexpected preset catalog version or size.' }
$frame = (Invoke-RestMethod "$BaseUrl/api/algorithm-runs/$($runtime.algorithmRunId)/frame" -WebSession $session).data
if (!$frame -or $frame.metrics.algorithmCommandProfile -ne $catalog.version) { throw 'The selected scene does not use the expected algorithm profile.' }
$targets = @($frame.targets | Where-Object { $_.visible -ne $false } | ForEach-Object code)
$devices = @($frame.agents | ForEach-Object { if ($_.deviceCode) { $_.deviceCode } else { $_.code } })
$cases = @()
foreach ($preset in $catalog.items) {
  $targetMissing = $false
  foreach ($match in [regex]::Matches($preset.command, 'T([12])')) {
    if ($targets -notcontains ('THREAT-00' + $match.Groups[1].Value)) { $targetMissing = $true }
  }
  $cases += @{ Text = "执行第$($preset.id)号指令"; Status = $(if ($targetMissing) { 'UNSUPPORTED' } else { 'CANDIDATE' }); Preset = $preset.id }
  $cases += @{ Text = $preset.label; Status = $(if ($targetMissing) { 'UNSUPPORTED' } else { 'CANDIDATE' }); Preset = $preset.id }
}
$cases += @(
  @{ Text = '一号无人机追击一号d穿。'; Status = 'CANDIDATE'; Preset = 1 },
  @{ Text = '一号无人机追击二号d穿。'; Status = 'CANDIDATE'; Preset = 7 },
  @{ Text = '二号无人艇追击二号地船。'; Status = 'CANDIDATE'; Preset = 4 },
  @{ Text = '三号无人机追击二号di chuan。'; Status = 'CANDIDATE'; Preset = 11 },
  @{ Text = '将一二三号无人艇分配给二号d穿。'; Status = 'CANDIDATE'; Preset = 14 },
  @{ Text = '不要一号无人机追击一号d穿'; Status = 'NOT_ACTIONABLE'; Reason = 'NEGATED_ACTION' },
  @{ Text = '一号无人机追击一号d穿吗？'; Status = 'NOT_ACTIONABLE'; Reason = 'QUESTION_ACTION' },
  @{ Text = '一号无人机追击二号的喘。'; Status = 'CANDIDATE'; Preset = 7 },
  @{ Text = '不要一号无人机追击二号的喘'; Status = 'NOT_ACTIONABLE'; Reason = 'NEGATED_ACTION' },
  @{ Text = '一号无人机追击二号的喘吗？'; Status = 'NOT_ACTIONABLE'; Reason = 'QUESTION_ACTION' },
  @{ Text = '让一架无人机追击一号敌船。'; Status = 'CANDIDATE'; Preset = 1 },
  @{ Text = '请让一架无人机追击一号敌船。'; Status = 'CANDIDATE'; Preset = 1 },
  @{ Text = '不要让一架无人机追击一号敌船'; Status = 'NOT_ACTIONABLE'; Reason = 'NEGATED_ACTION' },
  @{ Text = '让一架无人机追击一号敌船吗？'; Status = 'NOT_ACTIONABLE'; Reason = 'QUESTION_ACTION' },
  @{ Text = '将一二三号无人艇分配给二号的穿。'; Status = 'CANDIDATE'; Preset = 14 },
  @{ Text = '将一二三号无人艇分配给二号的船。'; Status = 'CANDIDATE'; Preset = 14 },
  @{ Text = '将一二三号无人艇分配给二号机船。'; Status = 'CANDIDATE'; Preset = 14 },
  @{ Text = '不要将一二三号无人艇分配给二号的穿'; Status = 'NOT_ACTIONABLE'; Reason = 'NEGATED_ACTION' },
  @{ Text = '将一二三号无人艇分配给二号的穿吗？'; Status = 'NOT_ACTIONABLE'; Reason = 'QUESTION_ACTION' },
  @{ Text = '恢复首位。'; Status = 'CANDIDATE'; Preset = 19 },
  @{ Text = '请恢复首位！'; Status = 'CANDIDATE'; Preset = 19 },
  @{ Text = '不要恢复首位'; Status = 'NOT_ACTIONABLE'; Reason = 'NEGATED_ACTION' },
  @{ Text = '恢复首位吗？'; Status = 'NOT_ACTIONABLE'; Reason = 'QUESTION_ACTION' },
  @{ Text = '一号无人机追击一号机船。'; Status = 'CANDIDATE'; Preset = 1 },
  @{ Text = '一号无人机追击一号的船。'; Status = 'CANDIDATE'; Preset = 1 },
  @{ Text = '二号无人艇追击二号的船'; Status = 'CANDIDATE'; Preset = 4 },
  @{ Text = '不要一号无人机追击一号的船'; Status = 'NOT_ACTIONABLE'; Reason = 'NEGATED_ACTION' },
  @{ Text = '一号无人机追击一号的船吗？'; Status = 'NOT_ACTIONABLE'; Reason = 'QUESTION_ACTION' },
  @{ Text = '请让1号无人机追击第1号机船'; Status = 'CANDIDATE'; Preset = 1 },
  @{ Text = '不要一号无人机追击一号机船'; Status = 'NOT_ACTIONABLE'; Reason = 'NEGATED_ACTION' },
  @{ Text = '一号无人机追击一号机船吗？'; Status = 'NOT_ACTIONABLE'; Reason = 'QUESTION_ACTION' },
  @{ Text = '执行第13条指令'; Status = 'CANDIDATE'; Preset = 13 },
  @{ Text = '执行第十三条指令'; Status = 'CANDIDATE'; Preset = 13 },
  @{ Text = '执行第十三个指令'; Status = 'CANDIDATE'; Preset = 13 },
  @{ Text = '请帮我执行第十三号命令吧'; Status = 'CANDIDATE'; Preset = 13 },
  @{ Text = '开始执行第十三号指令'; Status = 'CANDIDATE'; Preset = 13 },
  @{ Text = '指令XIII'; Status = 'CANDIDATE'; Preset = 13 },
  @{ Text = '执行第ⅩⅢ条指令'; Status = 'CANDIDATE'; Preset = 13 },
  @{ Text = '执行第１３条指令'; Status = 'CANDIDATE'; Preset = 13 },
  @{ Text = '执行第壹拾叁条指令'; Status = 'CANDIDATE'; Preset = 13 },
  @{ Text = '执行围捕观察全过程。'; Status = 'CANDIDATE'; Preset = 20 },
  @{ Text = '执行围捕，观察全过程。'; Status = 'CANDIDATE'; Preset = 20 },
  @{ Text = '不要执行围捕，观察全过程'; Status = 'NOT_ACTIONABLE'; Reason = 'NEGATED_ACTION' },
  @{ Text = '执行围捕，观察全过程吗？'; Status = 'NOT_ACTIONABLE'; Reason = 'QUESTION_ACTION' },
  @{ Text = '不要执行二十号指令'; Status = 'NOT_ACTIONABLE'; Reason = 'NEGATED_ACTION' },
  @{ Text = '不要第十三号指令'; Status = 'NOT_ACTIONABLE'; Reason = 'NEGATED_ACTION' },
  @{ Text = '不要第十三条指令'; Status = 'NOT_ACTIONABLE'; Reason = 'NEGATED_ACTION' },
  @{ Text = '不要指令ⅩⅢ'; Status = 'NOT_ACTIONABLE'; Reason = 'NEGATED_ACTION' },
  @{ Text = '执行二十号指令吗？'; Status = 'NOT_ACTIONABLE'; Reason = 'QUESTION_ACTION' },
  @{ Text = '执行第ⅩⅢ条指令吗？'; Status = 'NOT_ACTIONABLE'; Reason = 'QUESTION_ACTION' },
  @{ Text = '启动XXI'; Status = 'NEEDS_CLARIFICATION'; Reason = 'AMBIGUOUS_ACTION' },
  @{ Text = '开始执行第二十一号指令'; Status = 'NEEDS_CLARIFICATION'; Reason = 'AMBIGUOUS_ACTION' },
  @{ Text = '开始任务，然后执行一个指令'; Status = 'NEEDS_CLARIFICATION'; Reason = 'AMBIGUOUS_ACTION' }
)
$results = @()
foreach ($case in $cases) {
  $current = (Invoke-RestMethod "$BaseUrl/api/voice/contexts/$($runtime.runtimeRef)" -WebSession $session).data
  if ($current.runtimeGeneration -ne $runtime.runtimeGeneration) { throw 'Scene changed during verification; rerun against the current scene.' }
  $id = [guid]::NewGuid().ToString()
  $body = @{
    requestId = $id; text = $case.Text; locale = 'zh-CN'; allowedActions = @('START','PAUSE','RESUME','STOP')
    availableDeviceCodes = $devices
    runtimeContext = @{ runtimeRef = $current.runtimeRef; runtimeGeneration = $current.runtimeGeneration; contextVersion = $current.contextVersion }
  } | ConvertTo-Json -Depth 6
  $headers = @{ $csrf.headerName = $csrf.token; 'X-Request-ID' = $id; 'Idempotency-Key' = $id }
  $parsed = (Invoke-RestMethod "$BaseUrl/api/voice/intelligence/interpretations" -Method Post -WebSession $session -Headers $headers -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))).data
  $passed = $parsed.status -eq $case.Status
  if ($case.Reason) { $passed = $passed -and $parsed.reason -eq $case.Reason }
  if ($case.Status -eq 'CANDIDATE') { $passed = $passed -and $parsed.action -eq 'ALGORITHM_PRESET' -and $parsed.parameters.presetId -eq $case.Preset -and $parsed.parameters.presetVersion -eq $catalog.version }
  $results += [pscustomobject]@{ Text = $case.Text; NormalizedText = $parsed.normalizedText; Passed = $passed; ExpectedStatus = $case.Status; ActualStatus = $parsed.status; Preset = $parsed.parameters.presetId; Reason = $parsed.reason }
}
$report = [pscustomobject]@{
  Timestamp = [DateTimeOffset]::UtcNow.ToString('o'); Profile = $catalog.version
  Mode = 'Text interpretation only; existing scene preserved; no proposals or commands dispatched'
  Passed = @($results | Where-Object Passed).Count; Total = $results.Count; Cases = $results
}
$output = $report | ConvertTo-Json -Depth 8
if ($ReportPath) { [IO.File]::WriteAllText([IO.Path]::GetFullPath($ReportPath), $output, [Text.UTF8Encoding]::new($false)) }
Write-Output $output
if ($report.Passed -ne $report.Total) { throw 'Preset verification failed; inspect Cases in the report.' }
