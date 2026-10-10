import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'

import VirtualFleetConfigView from '@/views/VirtualFleetConfigView.vue'
import { simulationRuntime } from '@/composables/simulationRuntime'
import { useAuthStore } from '@/stores/auth'
import type { AlgorithmRuntimeFrame } from '@/types/mission'

vi.mock('@/components/layout/ConsoleLayout.vue', () => ({ default: { template: '<main><slot /></main>' } }))
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
    global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
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
    algorithmCode: 'GB_SFLA_CS', phase: 'COMPLETED',
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
