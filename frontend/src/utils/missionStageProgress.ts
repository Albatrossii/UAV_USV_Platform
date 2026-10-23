/** Read-only stage facts. This never estimates progress from elapsed time. */
export interface MissionStageProgress {
  label: string
  value: string
  detail?: string
}

export interface MissionStageProgressInput {
  phase?: string
  metrics?: Record<string, unknown>
  terminalStatus?: string | null
}

type Metrics = Record<string, unknown>
const ringStages = new Set(['ENCIRCLEMENT', 'ENCIRCLING', 'GAP_REPAIR', 'STABLE_CONTAINMENT', 'CONTAINMENT', 'CAPTURE_HOLD', 'RECONFIGURING'])
const pursuitStages = new Set(['PURSUIT', 'ESCAPE_PURSUIT', 'TARGET_ESCAPE'])
const completedStates = new Set(['COMPLETED', 'CAPTURED', 'SECURED', 'ESCAPED'])

function record(value: unknown): Metrics | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Metrics : null
}

function code(value: unknown): string {
  return typeof value === 'string' ? value.trim().toUpperCase() : ''
}

function nonnegative(...values: unknown[]): number | undefined {
  return values.find((value): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0)
}

function count(...values: unknown[]): number | undefined {
  return values.find((value): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0)
}

function positiveCount(...values: unknown[]): number | undefined {
  return values.find((value): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value > 0)
}

function measuredRatio(value: unknown): number | undefined {
  const number = nonnegative(value)
  return number !== undefined && number <= 1 ? number : undefined
}

function format(value: number): string {
  return value.toFixed(1).replace(/\.0$/, '')
}

function progress(label: string, facts: string[], subject = ''): MissionStageProgress | null {
  if (!facts.length) return null
  const [value, ...rest] = facts
  const detail = [subject, ...rest].filter(Boolean).join(' · ')
  return { label, value: value!, ...(detail ? { detail } : {}) }
}

function finished(group: Metrics): boolean {
  return completedStates.has(code(group.state)) || code(group.missionStage) === 'COMPLETED'
}

function stageOf(group: Metrics, fallback: string): string {
  if (code(group.localAction) === 'GAP_REPAIR') return 'GAP_REPAIR'
  return code(group.missionStage) || code(group.state) || fallback
}

/** Prefer one unfinished subject; never mix one target's distance with another's ring. */
function stageSubject(metrics: Metrics, stage: string): Metrics | null {
  if (!Array.isArray(metrics.captureGroups) || !metrics.captureGroups.length) return metrics
  const groups = metrics.captureGroups.map(record).filter((group): group is Metrics => group !== null && !finished(group))
  const subject = code(metrics.stageSubjectThreatCode)
  return groups.find(group => subject && code(group.threatCode) === subject)
    ?? groups.find(group => stageOf(group, stage) === stage)
    ?? groups[0]
    ?? null
}

function getStageFacts(input: MissionStageProgressInput | null | undefined): MissionStageProgress | null {
  if (!input) return null
  const metrics = record(input.metrics)
  if (!metrics) return null
  const phase = code(input.phase)
  const stage = code(metrics.missionStage) || phase
  if (code(input.terminalStatus) === 'COMPLETED') return null

  const deployment = code(metrics.deploymentState) || phase
  if (deployment === 'PARKED' || deployment === 'DEPLOYING') {
    const ratio = measuredRatio(metrics.deploymentProgress)
    const ready = count(metrics.deploymentReadyCount)
    const total = positiveCount(metrics.deploymentTotalCount)
    const withdrawn = count(metrics.deploymentWithdrawnCount)
    const deferred = count(metrics.deploymentDeferredCount)
    // The backend ratio excludes settled withdrawals; the count names them
    // separately instead of silently marking them as successfully departed.
    const facts: string[] = []
    if (ratio !== undefined) facts.push(`${Math.round(ratio * 100)}%`)
    if (ready !== undefined && total !== undefined && ready <= total) facts.push(`已就位 ${ready}/${total} 台`)
    else if (ready !== undefined) facts.push(`已就位 ${ready} 台`)
    if (facts.length && withdrawn !== undefined && withdrawn > 0) facts.push(`退出 ${withdrawn} 台`)
    if (facts.length && deferred !== undefined && deferred > 0) facts.push(`人工接管 ${deferred} 台`)
    return progress(code(metrics.deploymentMode) === 'SAFE_RELEASE' ? '出发进度' : '集结进度', facts)
  }

  if (phase === 'ASSEMBLING_ESCORT' || code(metrics.escortAssemblyState) === 'ASSEMBLING') {
    const ratio = measuredRatio(metrics.escortAssemblyProgress)
    const ready = count(metrics.escortProtectionReadyCount)
    const required = positiveCount(metrics.escortProtectionRequiredCount)
    const distance = nonnegative(metrics.escortAssemblyDistanceM)
    const facts: string[] = []
    if (ratio !== undefined) facts.push(`${format(ratio === 1 ? 100 : Math.min(99.9, Math.round(ratio * 1000) / 10))}%`)
    if (ready !== undefined && required !== undefined && ready <= required) facts.push(`${ratio !== undefined ? '已到位 ' : ''}${ready}/${required} 台`)
    else if (ready !== undefined) facts.push(`${ready} 台已就位`)
    if (distance !== undefined) facts.push(`最远距槽位 ${distance.toFixed(1)} m`)
    return progress(ratio !== undefined ? '近卫进度' : '近护就位', facts)
  }

  if (phase === 'COMPLETED' || stage === 'COMPLETED') return null

  const selected = stageSubject(metrics, stage)
  if (!selected) return null
  const subjectStage = stageOf(selected, stage)
  const subject = selected === metrics ? '' : code(selected.threatCode)
  if (pursuitStages.has(subjectStage)) {
    const distance = nonnegative(selected.pursuitDistanceM, selected.targetTravelDistanceM)
    const required = nonnegative(selected.requiredPursuitDistanceM)
    if (distance === undefined) return null
    return progress('追击进展', [required !== undefined && required > 0
      ? `${format(distance)} / ${format(required)} m` : `${format(distance)} m`], subject)
  }
  if (!ringStages.has(subjectStage)) return null

  const canonical = record(selected.canonicalContainmentContract) ?? {}
  const containment = record(selected.containmentContract) ?? {}
  const members = positiveCount(selected.requiredMemberCount, selected.ringMemberCount, selected.memberCount, selected.requiredCaptureAgents)
  let arrived = count(selected.arrivalCount, selected.arrivedMemberCount, canonical.arrivalCount)
  if (arrived === undefined && members !== undefined) {
    const ratio = measuredRatio(selected.arrivalRatio) ?? measuredRatio(canonical.arrivalRatio)
    if (ratio !== undefined) arrived = Math.round(ratio * members)
  }
  const error = nonnegative(canonical.maximumSlotErrorM, selected.maximumSlotErrorM, selected.maximumSlotError, selected.radialErrorM)
  const gap = nonnegative(canonical.maxGapDeg, selected.postGlobalMaxGapDeg, selected.maxAngularGapDeg, selected.combinedMaxGapDeg, containment.maxGapDeg)
  const hold = count(selected.postGlobalHoldFrames, selected.holdFrames, selected.captureHoldFrames, selected.stableContainmentFrames)
  const requiredHold = positiveCount(selected.postGlobalHoldRequiredFrames, selected.holdRequiredFrames,
    selected.requiredCaptureHoldFrames, selected.stableContainmentRequiredFrames, selected.requiredStableContainmentFrames)
  const facts: string[] = []
  if (arrived !== undefined && members !== undefined && arrived <= members) facts.push(`到位 ${arrived}/${members} 台`)
  else if (arrived !== undefined && members === undefined) facts.push(`到位 ${arrived} 台`)
  if (error !== undefined) facts.push(`最大槽位误差 ${format(error)} m`)
  if (gap !== undefined && gap <= 360) facts.push(`最大缺口 ${format(gap)}°`)
  if (hold !== undefined) facts.push(requiredHold !== undefined
    ? `连续保持 ${hold}/${requiredHold} 帧` : `连续保持 ${hold} 帧`)
  return progress(subjectStage === 'GAP_REPAIR' ? '缺口修复'
    : ['STABLE_CONTAINMENT', 'CONTAINMENT', 'CAPTURE_HOLD'].includes(subjectStage) ? '稳定围控' : '围捕进展', facts, subject)
}

/** The runtime has already composed overall progress; do not weight it again here. */
export function getMissionStageProgress(input: MissionStageProgressInput | null | undefined): MissionStageProgress | null {
  const facts = getStageFacts(input)
  const metrics = record(input?.metrics)
  if (!input || !metrics) return facts
  const phase = code(input.phase)
  const deployment = code(metrics.deploymentState) || phase
  if (code(input.terminalStatus) === 'COMPLETED'
    || phase === 'COMPLETED' || code(metrics.missionStage) === 'COMPLETED'
    || deployment === 'PARKED' || deployment === 'DEPLOYING'
    || phase === 'ASSEMBLING_ESCORT' || code(metrics.escortAssemblyState) === 'ASSEMBLING') return facts
  const task = measuredRatio(metrics.taskProgress)
  if (task === undefined) return facts
  return progress('任务进度', [
    `${format(Math.round(task * 1000) / 10)}%`,
    ...(facts ? [`${facts.label} ${facts.value}`, facts.detail ?? ''].filter(Boolean) : []),
  ])
}
