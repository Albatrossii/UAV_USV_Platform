import { describe, expect, it } from 'vitest'
import { getMissionStageProgress } from '../missionStageProgress'

const stage = (missionStage: string, metrics: Record<string, unknown> = {}) => ({
  phase: missionStage, metrics: { missionStage, ...metrics },
})

describe('measured mission stage progress', () => {
  it.each([null, undefined, {}, { metrics: null }])('ignores absent metrics: %j', input => {
    expect(getMissionStageProgress(input as Parameters<typeof getMissionStageProgress>[0])).toBeNull()
  })

  it.each(['PARKED', 'DEPLOYING'])('shows factual departure ratio and counts for %s', deploymentState => {
    expect(getMissionStageProgress(stage(deploymentState, {
      deploymentState, deploymentMode: 'SAFE_RELEASE', deploymentProgress: .625, deploymentReadyCount: 2,
      deploymentTotalCount: 6, deploymentWithdrawnCount: 1,
    }))).toEqual({ label: '出发进度', value: '63%', detail: '已就位 2/6 台 · 退出 1 台' })
  })

  it('distinguishes an operator-deferred device from a departed or withdrawn device', () => {
    expect(getMissionStageProgress(stage('DEPLOYING', {
      deploymentState: 'DEPLOYING', deploymentMode: 'SAFE_RELEASE', deploymentProgress: .8,
      deploymentReadyCount: 4, deploymentTotalCount: 6, deploymentDeferredCount: 1,
    }))).toEqual({ label: '出发进度', value: '80%', detail: '已就位 4/6 台 · 人工接管 1 台' })
  })

  it('does not fabricate a departure ratio from elapsed time or ready counts', () => {
    expect(getMissionStageProgress(stage('DEPLOYING', {
      deploymentElapsedSeconds: 90, deploymentReadyCount: 2, deploymentTotalCount: 6,
    }))).toEqual({ label: '集结进度', value: '已就位 2/6 台' })
    expect(getMissionStageProgress(stage('DEPLOYING', { deploymentElapsedSeconds: 500 }))).toBeNull()
  })

  it('retains real zero while rejecting non-finite, string and out-of-range ratios', () => {
    expect(getMissionStageProgress(stage('PARKED', { deploymentProgress: 0 }))?.value).toBe('0%')
    for (const deploymentProgress of [null, NaN, Infinity, -1, 2, '0.5']) {
      expect(getMissionStageProgress(stage('DEPLOYING', { deploymentProgress }))).toBeNull()
    }
  })

  it.each(['FULL_ASSEMBLY', undefined])('preserves legacy assembly labeling for %s', deploymentMode => {
    expect(getMissionStageProgress(stage('DEPLOYING', { deploymentMode, deploymentProgress: .4 })))
      .toEqual({ label: '集结进度', value: '40%' })
  })

  it('prioritizes physical departure and close protection over a stale completed stage', () => {
    const departing = stage('COMPLETED', { deploymentState: 'DEPLOYING', deploymentMode: 'SAFE_RELEASE', deploymentProgress: .6 })
    expect(getMissionStageProgress(departing)).toEqual({ label: '出发进度', value: '60%' })
    expect(getMissionStageProgress(stage('COMPLETED', {
      escortAssemblyState: 'ASSEMBLING', escortProtectionReadyCount: 1, escortProtectionRequiredCount: 2,
    }))).toEqual({ label: '近护就位', value: '1/2 台' })
    expect(getMissionStageProgress({ ...departing, terminalStatus: 'COMPLETED' })).toBeNull()
  })

  it('shows actual close guard readiness and one-decimal distance', () => {
    expect(getMissionStageProgress(stage('GUARDING', {
      escortAssemblyState: 'ASSEMBLING', escortProtectionReadyCount: 1,
      escortProtectionRequiredCount: 2, escortAssemblyDistanceM: 25,
    }))).toEqual({ label: '近护就位', value: '1/2 台', detail: '最远距槽位 25.0 m' })
  })

  it('shows actual approach percentage before the first guard arrives', () => {
    expect(getMissionStageProgress(stage('ASSEMBLING_ESCORT', {
      escortAssemblyState: 'ASSEMBLING', escortAssemblyProgress: .526,
      escortProtectionReadyCount: 0, escortProtectionRequiredCount: 2, escortAssemblyDistanceM: 89.25,
    }))).toEqual({ label: '近卫进度', value: '52.6%', detail: '已到位 0/2 台 · 最远距槽位 89.3 m' })
    expect(getMissionStageProgress(stage('ASSEMBLING_ESCORT', { escortAssemblyProgress: .999999 }))?.value).toBe('99.9%')
    for (const escortAssemblyProgress of [NaN, Infinity, null, '0.5', -1, 2]) {
      expect(getMissionStageProgress(stage('ASSEMBLING_ESCORT', {
        escortAssemblyProgress, escortProtectionReadyCount: 0, escortProtectionRequiredCount: 2,
      }))).toEqual({ label: '近护就位', value: '0/2 台' })
    }
  })

  it('shows original task progress after departure and protection without reweighting it', () => {
    expect(getMissionStageProgress(stage('GUARDING', {
      deploymentState: 'COMPLETE', escortAssemblyState: 'READY', taskProgress: .08, missionProgress: .356,
    }))).toEqual({ label: '任务进度', value: '8%' })
    expect(getMissionStageProgress(stage('ENCIRCLEMENT', {
      taskProgress: .5, missionProgress: .65, arrivalCount: 3, memberCount: 4, maximumSlotErrorM: 12,
    }))).toEqual({ label: '任务进度', value: '50%', detail: '围捕进展 到位 3/4 台 · 最大槽位误差 12 m' })
  })

  it('keeps departure and guard components ahead of task progress, including paused frames', () => {
    const input = stage('PAUSED', { deploymentState: 'DEPLOYING', deploymentMode: 'SAFE_RELEASE',
      deploymentProgress: .5, taskProgress: 0, missionProgress: .05 })
    expect(getMissionStageProgress(input)).toEqual({ label: '出发进度', value: '50%' })
    expect(getMissionStageProgress(stage('PAUSED', {
      escortAssemblyState: 'ASSEMBLING', escortAssemblyProgress: .5, taskProgress: 0, missionProgress: .2,
    }))).toEqual({ label: '近卫进度', value: '50%' })
    expect(getMissionStageProgress({ ...input, terminalStatus: 'COMPLETED' })).toBeNull()
  })

  it('shows assembly distance independently without inventing absent readiness counts', () => {
    expect(getMissionStageProgress(stage('ASSEMBLING_ESCORT', { escortAssemblyDistanceM: 9.274 })))
      .toEqual({ label: '近护就位', value: '最远距槽位 9.3 m' })
    expect(getMissionStageProgress(stage('ASSEMBLING_ESCORT', {
      escortProtectionReadyCount: null, escortProtectionRequiredCount: 2, escortAssemblyDistanceM: Infinity,
    }))).toBeNull()
  })

  it('reads single capture pursuit metrics without clamping real travelled distance', () => {
    expect(getMissionStageProgress(stage('PURSUIT', {
      targetTravelDistanceM: 42.36, requiredPursuitDistanceM: 36,
    }))).toEqual({ label: '追击进展', value: '42.4 / 36 m' })
    expect(getMissionStageProgress(stage('PURSUIT', { pursuitDistanceM: 0 })))
      .toEqual({ label: '追击进展', value: '0 m' })
    expect(getMissionStageProgress(stage('PURSUIT', { requiredPursuitDistanceM: 36 }))).toBeNull()
  })

  it('uses the selected unfinished escort threat rather than the completed first group', () => {
    expect(getMissionStageProgress(stage('PURSUIT', {
      stageSubjectThreatCode: 'THREAT-002', pursuitDistanceM: 999,
      captureGroups: [
        { threatCode: 'THREAT-001', state: 'CAPTURED', missionStage: 'COMPLETED', pursuitDistanceM: 180 },
        { threatCode: 'THREAT-002', missionStage: 'PURSUIT', pursuitDistanceM: 18.2, requiredPursuitDistanceM: 40 },
      ],
    }))).toEqual({ label: '追击进展', value: '18.2 / 40 m', detail: 'THREAT-002' })
  })

  it('falls back to an unfinished target when the subject is stale', () => {
    expect(getMissionStageProgress(stage('ENCIRCLEMENT', {
      stageSubjectThreatCode: 'THREAT-001', captureGroups: [
        { threatCode: 'THREAT-001', state: 'CAPTURED', arrivalCount: 6, memberCount: 6 },
        { threatCode: 'THREAT-002', missionStage: 'ENCIRCLEMENT', arrivalCount: 2, memberCount: 4 },
      ],
    }))).toEqual({ label: '围捕进展', value: '到位 2/4 台', detail: 'THREAT-002' })
  })

  it('does not borrow aggregate or another target metrics when the selected group lacks evidence', () => {
    expect(getMissionStageProgress(stage('ENCIRCLEMENT', {
      stageSubjectThreatCode: 'THREAT-002', maximumSlotErrorM: 3, captureGroups: [
        { threatCode: 'THREAT-001', missionStage: 'ENCIRCLEMENT', arrivalCount: 6, memberCount: 6 },
        { threatCode: 'THREAT-002', missionStage: 'ENCIRCLEMENT' },
      ],
    }))).toBeNull()
  })

  it('reads single capture root metric aliases and preserves a measured zero hold', () => {
    expect(getMissionStageProgress(stage('ENCIRCLEMENT', {
      arrivalRatio: .66, requiredCaptureAgents: 6, maximumSlotError: 8.64,
      combinedMaxGapDeg: 128.24, captureHoldFrames: 0, requiredCaptureHoldFrames: 20,
    }))).toEqual({
      label: '围捕进展', value: '到位 4/6 台',
      detail: '最大槽位误差 8.6 m · 最大缺口 128.2° · 连续保持 0/20 帧',
    })
  })

  it('prioritizes explicit backend arrival counts over rounded ratios', () => {
    expect(getMissionStageProgress(stage('ENCIRCLEMENT', {
      arrivalCount: 2, memberCount: 6, arrivalRatio: .99,
    }))?.value).toBe('到位 2/6 台')
  })

  it('uses executed multi-capture counts and does not count support members as ring participants', () => {
    expect(getMissionStageProgress(stage('ENCIRCLEMENT', { captureGroups: [{
      threatCode: 'TARGET-002', missionStage: 'ENCIRCLEMENT', memberCount: 10,
      arrivedMemberCount: 3, requiredMemberCount: 4, arrivalRatio: .75,
      maximumSlotErrorM: 11, postGlobalMaxGapDeg: 141,
    }] }))).toEqual({ label: '围捕进展', value: '到位 3/4 台',
      detail: 'TARGET-002 · 最大槽位误差 11 m · 最大缺口 141°' })
  })

  it('uses canonical escort slot error and gap before weaker legacy diagnostics', () => {
    expect(getMissionStageProgress(stage('ENCIRCLEMENT', { captureGroups: [{
      threatCode: 'THREAT-001', missionStage: 'ENCIRCLEMENT', memberCount: 4, arrivalRatio: .5,
      radialErrorM: 99, maxAngularGapDeg: 300,
      canonicalContainmentContract: { maximumSlotErrorM: 7.25, maxGapDeg: 121.35 },
      holdFrames: 3, holdRequiredFrames: 25,
    }] }))).toEqual({ label: '围捕进展', value: '到位 2/4 台',
      detail: 'THREAT-001 · 最大槽位误差 7.3 m · 最大缺口 121.3° · 连续保持 3/25 帧' })
  })

  it('shows slot error improving from 70 to 8 m even with unchanged arrival counts', () => {
    const input = stage('ENCIRCLEMENT', { arrivalCount: 5, memberCount: 6, maximumSlotErrorM: 70 })
    expect(getMissionStageProgress(input)?.detail).toBe('最大槽位误差 70 m')
    input.metrics.maximumSlotErrorM = 8
    expect(getMissionStageProgress(input)?.detail).toBe('最大槽位误差 8 m')
  })

  it('shows local gap repair and prioritizes executed consecutive hold counters', () => {
    expect(getMissionStageProgress(stage('STABLE_CONTAINMENT', { captureGroups: [{
      threatCode: 'TARGET-001', missionStage: 'STABLE_CONTAINMENT', localAction: 'GAP_REPAIR',
      postGlobalHoldFrames: 0, postGlobalHoldRequiredFrames: 25, holdFrames: 20, holdRequiredFrames: 20,
    }] }))).toEqual({ label: '缺口修复', value: '连续保持 0/25 帧', detail: 'TARGET-001' })
  })

  it('only displays available facts without replacing missing values with zero', () => {
    expect(getMissionStageProgress(stage('ENCIRCLEMENT', {
      arrivalRatio: null, maximumSlotErrorM: null, combinedMaxGapDeg: NaN,
      captureHoldFrames: null, requiredCaptureHoldFrames: 20,
    }))).toBeNull()
    expect(getMissionStageProgress(stage('ENCIRCLEMENT', {
      arrivalCount: NaN, memberCount: Infinity, maximumSlotErrorM: 8,
      holdFrames: null, holdRequiredFrames: 25, maxAngularGapDeg: 900,
    }))).toEqual({ label: '围捕进展', value: '最大槽位误差 8 m' })
  })

  it('does not synthesize arrival counts without a reported member count', () => {
    expect(getMissionStageProgress(stage('ENCIRCLEMENT', { arrivalRatio: .5 }))).toBeNull()
    expect(getMissionStageProgress(stage('ENCIRCLEMENT', { arrivalCount: 2 })))
      .toEqual({ label: '围捕进展', value: '到位 2 台' })
  })

  it('does not change facts with paused state, wall time, or repeated reads', () => {
    const metrics = Object.freeze({ missionStage: 'ENCIRCLEMENT', memberCount: 6, arrivalCount: 4,
      maximumSlotErrorM: 8, holdFrames: 3, holdRequiredFrames: 25 })
    const running = getMissionStageProgress({ phase: 'RUNNING', metrics })
    expect(getMissionStageProgress({ phase: 'PAUSED', metrics })).toEqual(running)
    expect(getMissionStageProgress({ phase: 'PAUSED', metrics: { ...metrics, simulationElapsedSeconds: 999 } })).toEqual(running)
    expect(getMissionStageProgress({ phase: 'RUNNING', metrics })).toEqual(running)
  })

  it('returns no stage facts after completion or when every target is complete', () => {
    const metrics = { memberCount: 4, arrivalCount: 4, holdFrames: 25, holdRequiredFrames: 25 }
    expect(getMissionStageProgress(stage('COMPLETED', metrics))).toBeNull()
    expect(getMissionStageProgress({ ...stage('ENCIRCLEMENT', metrics), terminalStatus: 'COMPLETED' })).toBeNull()
    expect(getMissionStageProgress(stage('ENCIRCLEMENT', { captureGroups: [
      { state: 'CAPTURED', ...metrics }, { state: 'SECURED', ...metrics },
    ] }))).toBeNull()
  })

  it('does not present unsupported phases or malformed groups as progress', () => {
    expect(getMissionStageProgress(stage('GUARDING', { arrivalCount: 0, memberCount: 6 }))).toBeNull()
    expect(getMissionStageProgress(stage('ENCIRCLEMENT', { captureGroups: [null, 42, 'invalid'] }))).toBeNull()
  })
})
