import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'

import VirtualFleetConfigView from '@/views/VirtualFleetConfigView.vue'
import { simulationRuntime } from '@/composables/simulationRuntime'
import { useAuthStore } from '@/stores/auth'
import type { AlgorithmRuntimeFrame } from '@/types/mission'

vi.mock('@/components/layout/ConsoleLayout.vue', () => ({ default: { template: '<main><slot /></main>' } }))
vi.mock('@/components/layout/ConsoleNavigationRail.vue', () => ({ default: { template: '<nav />' } }))
vi.mock('@/components/voice/VoiceP0ControlPanel.vue', () => ({
  default: { template: '<aside />', methods: { handleUnityMessage: vi.fn() } },
}))
vi.mock('@/api/algorithm', () => ({
  prepareAlgorithmRun: vi.fn().mockResolvedValue({}),
  controlAlgorithmRun: vi.fn(),
  fetchAlgorithmRunStatus: vi.fn().mockResolvedValue({ state: 'STOPPED', sequence: 0 }),
  fetchAlgorithmFrames: vi.fn().mockResolvedValue([]),
}))

interface ViewSetup {
  state: { algorithm: string; uavCount: number; usvCount: number; runId: number; mission: string; sequence: number }
  currentAlgorithmFrame: AlgorithmRuntimeFrame | null
  scenarioReadyRunId: number | null
  applyAlgorithmFrame: (frame: AlgorithmRuntimeFrame) => Promise<void>
}

const wrappers: VueWrapper[] = []
async function createView(algorithm = 'GB_SFLA_CS') {
  simulationRuntime.panel.value = {
    postToUnity: vi.fn().mockReturnValue('request-1'), postPresentationEnvelope: vi.fn().mockReturnValue(true),
    beginViewportTransition: vi.fn(), endViewportTransition: vi.fn(), syncViewport: vi.fn(), reload: vi.fn(),
  } as unknown as NonNullable<typeof simulationRuntime.panel.value>
  const wrapper = mount(VirtualFleetConfigView, {
    global: { stubs: { RouterLink: { template: '<a><slot /></a>' }, NodeCommunicationPanel: true } },
  })
  wrappers.push(wrapper)
  await flushPromises()
  const setup = wrapper.vm.$.setupState as unknown as ViewSetup
  Object.assign(setup.state, { algorithm, runId: 7001, mission: 'RUNNING', uavCount: 3, usvCount: 3, sequence: 0 })
  setup.scenarioReadyRunId = 7001
  return { wrapper, setup }
}

function frame(metrics: Record<string, unknown> = {}, terminalStatus: string | null = null): AlgorithmRuntimeFrame {
  return {
    runId: 7001, sequence: 1, timestamp: Date.now(), coordinateFrame: 'FLEET_LOCAL_ENU',
    algorithmCode: 'GB_SFLA_CS', phase: String(metrics.deploymentState === 'PARKED' || metrics.deploymentState === 'DEPLOYING'
      ? metrics.deploymentState : metrics.missionStage ?? 'COMPLETED'),
    agents: [], targets: [], route: [], obstacles: [], terminalStatus,
    metrics: { missionStage: 'COMPLETED', missionProgress: 1, capturedTargetCount: 1, ...metrics },
  } as AlgorithmRuntimeFrame
}

function displayed(wrapper: VueWrapper) {
  const values = wrapper.get('.vf-live-strip').findAll('strong')
  return {
    phase: values[0]!.text(), progress: values[1]!.text(),
    activeStep: wrapper.get('.vf-phase-stepper .active em').text(),
    terminalStepActive: wrapper.get('.vf-phase-stepper li:last-child').classes().includes('active'),
  }
}

describe('VirtualFleetConfigView authoritative mission completion display', () => {
  it.each(['GB_SFLA_CS', 'ESCORT_GUARD'])('labels %s near-shore release as departure instead of offshore assembly', async algorithm => {
    const { wrapper, setup } = await createView(algorithm)
    setup.currentAlgorithmFrame = frame({
      deploymentState: 'DEPLOYING', deploymentMode: 'SAFE_RELEASE', deploymentProgress: .6,
      deploymentTotalCount: 6, deploymentReadyCount: 3, deployingDeviceCount: 3, missionProgress: .06,
      taskProgress: 0, progressCompositionVersion: 'shore-lifecycle-v1',
    })
    await flushPromises()
    expect(wrapper.get('.vf-live-strip').text()).toContain('出发进度 60%')
    expect(wrapper.get('.vf-live-strip').text()).toContain('综合进度 6%')
    expect(wrapper.get('.vf-return-notice').text()).toContain('近岸释放点')
  })

  it('shows weighted overall and measured guard approach together before any guard has arrived', async () => {
    const { wrapper, setup } = await createView('ESCORT_GUARD')
    setup.currentAlgorithmFrame = frame({
      deploymentState: 'COMPLETE', missionStage: 'GUARDING', missionProgress: .2,
      taskProgress: 0, deploymentProgress: 1, progressCompositionVersion: 'shore-lifecycle-v1',
      escortAssemblyState: 'ASSEMBLING', escortAssemblyProgress: .5, escortProtectionReadyCount: 0, escortProtectionRequiredCount: 2,
      escortAssemblyDistanceM: 89.563, stageSubjectThreatCode: 'THREAT-001', simulationElapsedSeconds: 73,
    })
    setup.currentAlgorithmFrame.phase = 'ASSEMBLING_ESCORT'
    await flushPromises()
    expect(wrapper.get('.vf-live-strip').text()).toContain('近护动态就位')
    expect(wrapper.get('.vf-live-strip').text()).toContain('近卫进度 50%')
    expect(wrapper.get('.vf-stage-progress').text()).toContain('已到位 0/2 台')
    expect(wrapper.get('.vf-live-strip').text()).toContain('仿真时长 01:13')
    expect(wrapper.get('.vf-live-strip').text()).toContain('综合进度 20%')
    expect(wrapper.get('.vf-live-strip').text()).not.toContain('THREAT-001')
    expect(wrapper.get('.vf-return-notice').text()).toContain('保护形成前我方船保持待航')
    expect(wrapper.get('.vf-stage-progress').text()).toContain('89.6 m')
    setup.state.mission = 'PAUSED'
    await vi.advanceTimersByTimeAsync(5000)
    expect(wrapper.get('.vf-live-strip').text()).toContain('近卫进度 50%')
    expect(wrapper.get('.vf-live-strip').text()).toContain('综合进度 20%')
    expect(wrapper.get('.vf-stage-progress').text()).toContain('89.6 m')
    setup.state.mission = 'RUNNING'
    setup.currentAlgorithmFrame = { ...setup.currentAlgorithmFrame, sequence: 2, metrics: {
      ...setup.currentAlgorithmFrame.metrics, escortAssemblyDistanceM: 49.274, simulationElapsedSeconds: 80,
      escortAssemblyProgress: .75, missionProgress: .25,
    } }
    await flushPromises()
    expect(wrapper.get('.vf-stage-progress').text()).toContain('49.3 m')
    expect(wrapper.get('.vf-stage-progress').text()).toContain('近卫进度 75%')
    expect(wrapper.get('.vf-stage-progress').text()).toContain('已到位 0/2 台')
    expect(wrapper.get('.vf-live-strip').text()).toContain('综合进度 25%')
    expect(wrapper.get('.vf-live-strip').text()).toContain('仿真时长 01:20')
    setup.currentAlgorithmFrame = frame({
      deploymentState: 'COMPLETE', missionStage: 'ESCORTING', missionProgress: .356,
      taskProgress: .08, escortAssemblyProgress: 1, progressCompositionVersion: 'shore-lifecycle-v1',
      escortAssemblyState: 'READY', escortProtectionReadyCount: 2, escortProtectionRequiredCount: 2,
    })
    await flushPromises()
    expect(wrapper.get('.vf-live-strip').text()).toContain('综合进度 35.6%')
    expect(wrapper.get('.vf-stage-progress').text()).toContain('任务进度 8%')
    expect(wrapper.get('.vf-stage-progress').text()).not.toContain('近卫进度')
  })

  it.each([
    ['ESCORT_GUARD', .65, '近卫就位 20%'],
    ['GB_SFLA_CS', .55, '任务执行 90%'],
  ] as const)('renders composed %s progress once and shows the unweighted task progress separately', async (algorithm, overall, description) => {
    const { wrapper, setup } = await createView(algorithm)
    setup.currentAlgorithmFrame = frame({
      deploymentState: 'COMPLETE', deploymentProgress: 1, escortAssemblyState: 'READY',
      escortAssemblyProgress: 1, taskProgress: .5, missionProgress: overall,
      progressCompositionVersion: 'shore-lifecycle-v1', missionStage: 'ENCIRCLEMENT',
    })
    await flushPromises()
    expect(displayed(wrapper).progress).toBe(`${Math.round(overall * 1000) / 10}%`)
    expect(wrapper.get('.vf-stage-progress').text()).toContain('任务进度 50%')
    expect(wrapper.get('.vf-live-strip').findAll('span').some(item => item.attributes('title')?.includes(description))).toBe(true)
  })

  it.each(['GB_SFLA_CS', 'ESCORT_GUARD'])('shows measured %s assembly progress and the clock even before mission progress starts', async algorithm => {
    const { wrapper, setup } = await createView(algorithm)
    setup.currentAlgorithmFrame = frame({
      deploymentState: 'DEPLOYING', deploymentProgress: .56, deploymentTotalCount: 6,
      deploymentReadyCount: 2, deployingDeviceCount: 4, missionProgress: 0,
      simulationElapsedSeconds: 80,
    })
    await flushPromises()
    expect(wrapper.get('.vf-live-strip').text()).toContain('集结进度 56%')
    expect(wrapper.get('.vf-live-strip').text()).toContain('仿真时长 01:20')
    expect(wrapper.get('.vf-return-notice').text()).toContain('已就位 2/6 台')
    expect(wrapper.get('.vf-live-strip').text()).toContain('综合进度 0%')
    expect(wrapper.findAll('.vf-phase-stepper .active')).toHaveLength(0)

    setup.currentAlgorithmFrame = frame({
      deploymentState: 'COMPLETE', deploymentProgress: 1, missionStage: 'PURSUIT',
      missionProgress: .12, simulationElapsedSeconds: 90,
    })
    await flushPromises()
    expect(wrapper.get('.vf-live-strip').text()).toContain('综合进度 12%')
    expect(wrapper.get('.vf-live-strip').text()).toContain('仿真时长 01:30')
    expect(wrapper.get('.vf-live-strip').text()).not.toContain('集结进度')
  })

  it('does not count withdrawn devices as assembled or let elapsed wall time invent simulation progress', async () => {
    const { wrapper, setup } = await createView()
    setup.currentAlgorithmFrame = frame({
      deploymentState: 'DEPLOYING', deploymentProgress: .4, deploymentTotalCount: 6,
      deploymentReadyCount: 1, deploymentWithdrawnCount: 1, deployingDeviceCount: 4,
      simulationElapsedSeconds: 30, missionProgress: 0,
    })
    setup.state.mission = 'PAUSED'
    await flushPromises()
    const before = wrapper.get('.vf-live-strip').text()
    await vi.advanceTimersByTimeAsync(5000)
    expect(wrapper.get('.vf-live-strip').text()).toBe(before)
    expect(before).toContain('集结进度 40%')
    expect(before).toContain('已就位 1/6 台，1 台已返航退出集结')
  })

  it('shows that a held departure device is deferred while the remaining fleet keeps running', async () => {
    const { wrapper, setup } = await createView('ESCORT_GUARD')
    setup.currentAlgorithmFrame = frame({
      deploymentState: 'COMPLETE', deploymentProgress: 1, deploymentTotalCount: 6,
      deploymentReadyCount: 5, deploymentDeferredCount: 1,
      deploymentDeferredDeviceCodes: ['UAV-001'], missionStage: 'GUARDING',
      escortAssemblyState: 'ASSEMBLING', escortAssemblyProgress: .4,
      escortProtectionReadyCount: 0, escortProtectionRequiredCount: 2,
      missionProgress: .14,
    })
    setup.currentAlgorithmFrame.phase = 'ASSEMBLING_ESCORT'
    await flushPromises()
    const notice = wrapper.get('.vf-return-notice').text()
    expect(notice).toContain('UAV-001 已由人工接管并保持当前位置')
    expect(notice).toContain('其余设备继续执行任务')
    expect(notice).toContain('归队')
  })

  it.each(['PARKED', 'DEPLOYING'])('shows %s departure before activating mission stages or exposing the inner stage', async deploymentState => {
    const { wrapper, setup } = await createView()
    setup.currentAlgorithmFrame = frame({
      deploymentState, deployingDeviceCount: 6, missionStage: 'ESCAPE', missionProgress: 0,
      stageSubjectThreatCode: 'THREAT-001',
    })
    await flushPromises()
    const values = wrapper.get('.vf-live-strip').findAll('strong')
    expect(values[0]!.text()).toBe(deploymentState === 'PARKED' ? '岸边待命' : '安全起飞 / 离泊')
    expect(values[1]!.text()).toBe('0%')
    expect(wrapper.findAll('.vf-phase-stepper .active')).toHaveLength(0)
    expect(wrapper.get('.vf-return-notice').text()).toContain(deploymentState === 'PARKED' ? '原停机坪待命' : '6 台设备正在起飞')
    setup.currentAlgorithmFrame = frame({ deploymentState: 'COMPLETE', missionStage: 'ESCAPE', missionProgress: .02 })
    await flushPromises()
    expect(displayed(wrapper).activeStep).toBe('目标逃逸')
  })

  it.each([0.42, 0.75])('does not claim progress is held at 99 percent during an earlier return (%s)', async progress => {
    const { wrapper, setup } = await createView()
    setup.currentAlgorithmFrame = frame({ missionStage: 'ENCIRCLEMENT', missionProgress: progress, returningDeviceCount: 1, completionBlocker: 'WAITING_FOR_RETURN' })
    await flushPromises()
    expect(displayed(wrapper).progress).toBe(`${Math.round(progress * 100)}%`)
    expect(wrapper.get('.vf-return-notice').text()).toContain('进度以当前任务为准')
    expect(wrapper.get('.vf-return-notice').text()).not.toContain('99%')
  })

  it('renders measured progress to one decimal and never advances from wall-clock time alone', async () => {
    const { wrapper, setup } = await createView('ESCORT_GUARD')
    setup.currentAlgorithmFrame = frame({ missionStage: 'ENCIRCLEMENT', missionProgress: .731 })
    await flushPromises()
    expect(displayed(wrapper).progress).toBe('73.1%')
    await vi.advanceTimersByTimeAsync(30000)
    expect(displayed(wrapper).progress).toBe('73.1%')
    setup.currentAlgorithmFrame = frame({ missionStage: 'ENCIRCLEMENT', missionProgress: .734 })
    await flushPromises()
    expect(displayed(wrapper).progress).toBe('73.4%')
  })

  it.each([NaN, Infinity, -Infinity])('does not render invalid progress %s', async missionProgress => {
    const { wrapper, setup } = await createView()
    setup.currentAlgorithmFrame = frame({ missionStage: 'PURSUIT', missionProgress })
    await flushPromises()
    expect(displayed(wrapper).progress).toBe('0%')
  })

  it('shows closing distance even when the rounded overall progress or arrived count is unchanged', async () => {
    const { wrapper, setup } = await createView('ESCORT_GUARD')
    const captureGroup = {
      threatCode: 'THREAT-001', state: 'ENCIRCLING', missionStage: 'ENCIRCLEMENT',
      memberCount: 4, arrivalRatio: .75, holdFrames: 0, holdRequiredFrames: 25,
      canonicalContainmentContract: { maximumSlotErrorM: 64.61, maxGapDeg: 174.89 },
    }
    setup.currentAlgorithmFrame = frame({
      missionStage: 'ENCIRCLEMENT', missionProgress: .731, stageSubjectThreatCode: 'THREAT-001',
      captureGroups: [captureGroup],
    })
    await flushPromises()
    expect(displayed(wrapper).progress).toBe('73.1%')
    expect(wrapper.get('.vf-stage-progress').text()).toContain('到位 3/4 台')
    expect(wrapper.get('.vf-stage-progress').text()).toContain('最大槽位误差 64.6 m')
    setup.currentAlgorithmFrame = frame({
      missionStage: 'ENCIRCLEMENT', missionProgress: .731, stageSubjectThreatCode: 'THREAT-001',
      captureGroups: [{ ...captureGroup, canonicalContainmentContract: { maximumSlotErrorM: 40.48, maxGapDeg: 166.39 } }],
    })
    await flushPromises()
    expect(displayed(wrapper).progress).toBe('73.1%')
    expect(wrapper.get('.vf-stage-progress').text()).toContain('最大槽位误差 40.5 m')
    expect(wrapper.get('.vf-stage-progress').text()).toContain('最大缺口 166.4°')
  })

  it('explains the real completion hold at 99 percent while waiting for return', async () => {
    const { wrapper, setup } = await createView()
    setup.currentAlgorithmFrame = frame({ returningDeviceCount: 1, completionBlocker: 'WAITING_FOR_RETURN' })
    await flushPromises()
    expect(displayed(wrapper).progress).toBe('99%')
    expect(wrapper.get('.vf-return-notice').text()).toContain('尚未确认任务完成')
    expect(wrapper.get('.vf-return-notice').text()).toContain('保持在 99%')
  })

  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-10T09:00:00.000Z'))
    sessionStorage.clear()
    setActivePinia(createPinia())
    useAuthStore().user = { username: 'admin', role: 'ADMIN' }
    simulationRuntime.recovering.value = false
  })

  afterEach(() => {
    for (const wrapper of wrappers.splice(0)) wrapper.unmount()
    simulationRuntime.panel.value = null
    simulationRuntime.events = null
    simulationRuntime.recovering.value = false
    sessionStorage.clear()
    vi.useRealTimers()
  })

  it.each(['GB_SFLA_CS', 'ESCORT_GUARD'])('does not show completion while %s still has an operator-controlled agent', async algorithm => {
    const { wrapper, setup } = await createView(algorithm)
    setup.currentAlgorithmFrame = frame({
      missionProgress: 0.99, completionBlocker: 'OPERATOR_OVERRIDE', controlOverrideActive: true,
      operatorControlledDeviceCount: 1,
      deviceControlStates: { 'UAV-001': { controlAuthority: 'OPERATOR', motionState: 'HOLDING' } },
    })
    await flushPromises()
    expect(displayed(wrapper)).toEqual({
      phase: '稳定闭环 · 等待人工处理', progress: '99%', activeStep: '稳定闭环', terminalStepActive: false,
    })
    expect(wrapper.get('.vf-return-notice').text()).toContain('存在人工接管设备')
    expect(setup.state.mission).toBe('RUNNING')
  })

  it.each([
    ['WAITING_FOR_RETURN', '等待返航'],
    ['INSUFFICIENT_ACTIVE_FORCE', '等待编组恢复'],
  ])('keeps %s in a non-terminal stage even when the inner algorithm reports 100 percent', async (blocker, reason) => {
    const { wrapper, setup } = await createView()
    setup.currentAlgorithmFrame = frame({ completionBlocker: blocker })
    await flushPromises()
    expect(displayed(wrapper)).toEqual({
      phase: `稳定闭环 · ${reason}`, progress: '99%', activeStep: '稳定闭环', terminalStepActive: false,
    })
  })

  it.each(['GB_SFLA_CS', 'ESCORT_GUARD'])('does not infer %s terminal completion from stage, progress, or captured count alone', async algorithm => {
    const { wrapper, setup } = await createView(algorithm)
    setup.currentAlgorithmFrame = frame()
    await flushPromises()
    expect(displayed(wrapper).phase).not.toBe('完成')
    expect(displayed(wrapper).progress).toBe('99%')
    expect(displayed(wrapper).activeStep).toBe('稳定闭环')
    expect(displayed(wrapper).terminalStepActive).toBe(false)
  })

  it.each([
    ['GB_SFLA_CS', 'PURSUIT', '协同追击'],
    ['GB_SFLA_CS', 'ENCIRCLEMENT', '动态围捕'],
    ['ESCORT_GUARD', 'THREAT_DETECTION', '意图识别'],
  ])('keeps a normal %s / %s stage and its real progress during a paused operator override', async (algorithm, phase, label) => {
    const { wrapper, setup } = await createView(algorithm)
    setup.currentAlgorithmFrame = frame({
      missionStage: phase, missionProgress: 0.42, completionBlocker: 'OPERATOR_OVERRIDE',
      stageSubjectThreatCode: 'THREAT-001',
    })
    setup.state.mission = 'PAUSED'
    await flushPromises()
    expect(displayed(wrapper)).toEqual({
      phase: `${label} · THREAT-001`, progress: '42%', activeStep: label, terminalStepActive: false,
    })
  })

  it('waits for the matching Unity acknowledgement before showing completed / 100 percent / final step together', async () => {
    const { wrapper, setup } = await createView()
    await setup.applyAlgorithmFrame(frame({ missionProgress: 0.99 }, 'COMPLETED'))
    await flushPromises()
    expect(setup.state.mission).toBe('COMPLETING')
    expect(displayed(wrapper)).toEqual({
      phase: '稳定闭环 · 等待画面同步', progress: '99%', activeStep: '稳定闭环', terminalStepActive: false,
    })
    simulationRuntime.events!.message({ type: 'poseFrameApplied', payload: { success: true, runId: 7001, sequence: 0 } })
    await flushPromises()
    expect(displayed(wrapper).terminalStepActive).toBe(false)
    simulationRuntime.events!.message({ type: 'poseFrameApplied', payload: { success: true, runId: 7001, sequence: 1 } })
    await flushPromises()
    expect(setup.state.mission).toBe('COMPLETED')
    expect(displayed(wrapper)).toEqual({ phase: '完成', progress: '100%', activeStep: '完成', terminalStepActive: true })
  })

  it.each(['GB_SFLA_CS', 'ESCORT_GUARD'])('uses committed %s completion consistently even if the preceding frame was 99 percent', async algorithm => {
    const { wrapper, setup } = await createView(algorithm)
    setup.currentAlgorithmFrame = frame({ missionStage: 'STABLE_CONTAINMENT', missionProgress: 0.99 })
    setup.state.mission = 'COMPLETED'
    await flushPromises()
    expect(displayed(wrapper)).toEqual({ phase: '完成', progress: '100%', activeStep: '完成', terminalStepActive: true })
  })
})
