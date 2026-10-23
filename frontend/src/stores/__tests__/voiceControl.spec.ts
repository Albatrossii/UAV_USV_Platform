import { ApiClientError } from '@/api/http'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { useAuthStore } from '@/stores/auth'
import { useVoiceControlStore } from '@/stores/voiceControl'
import type {
  VoiceExecution,
  VoiceProposal,
  VoicePresentationReportRequest,
  VoiceRuntimeContext,
} from '@/types/voiceControl'

const voiceApi = vi.hoisted(() => ({
  cancelVoiceProposal: vi.fn(),
  confirmVoiceProposal: vi.fn(),
  createVoiceProposal: vi.fn(),
  createVoicePresentationChallenge: vi.fn(),
  fetchVoiceContexts: vi.fn(),
  fetchVoiceExecution: vi.fn(),
  fetchVoicePresentationBinding: vi.fn(),
  fetchVoiceProposal: vi.fn(),
  replaceVoicePresentationBinding: vi.fn(),
  reportVoicePresentation: vi.fn(),
}))

vi.mock('@/api/voiceControl', () => voiceApi)

const context: VoiceRuntimeContext = {
  runtimeRef: '11111111-1111-4111-8111-111111111111',
  runtimeGeneration: '22222222-2222-4222-8222-222222222222',
  contextVersion: 3,
  stateVersion: 2,
  runtimeScope: 'MISSION_CENTER',
  runtimeKind: 'STANDALONE_ALGORITHM',
  executionBackend: 'PYTHON_SIMULATION',
  algorithmRunId: '178980000001',
  missionId: null,
  missionRunId: null,
  state: 'RUNNING',
  protocolVersion: 'algorithm.command.v1',
  capabilities: ['START', 'PAUSE', 'RESUME', 'STOP'],
  lastHeartbeatReceivedAt: '2026-09-21T00:00:00.000Z',
  latestFrameSequence: 12,
  sceneReady: true,
}

function execution(overrides: Partial<VoiceExecution> = {}): VoiceExecution {
  return {
    executionId: '33333333-3333-4333-8333-333333333333',
    proposalId: '44444444-4444-4444-8444-444444444444',
    commandId: '55555555-5555-4555-8555-555555555555',
    runtimeRef: context.runtimeRef,
    runtimeGeneration: context.runtimeGeneration,
    action: 'START',
    state: 'SUCCEEDED',
    outcome: 'SUCCESS',
    errorCode: null,
    timedOutAt: null,
    presentationStatus: 'PENDING',
    createdAt: '2026-09-21T00:00:00.000Z',
    updatedAt: '2026-09-21T00:00:01.000Z',
    ...overrides,
  }
}

const proposal: VoiceProposal = {
  proposalId: '44444444-4444-4444-8444-444444444444',
  status: 'AWAITING_CONFIRMATION',
  planVersion: 1,
  planHash: 'abc123',
  plan: {
    runtimeRef: context.runtimeRef,
    runtimeGeneration: context.runtimeGeneration,
    contextVersion: context.contextVersion,
    stateVersion: context.stateVersion,
    action: 'START',
    explicitDeviceCodes: ['UAV-001', 'USV-001'],
    policyVersion: 'voice-p0.v1',
  },
  requiresConfirmation: true,
  createdAt: '2026-09-21T00:00:00.000Z',
  expiresAt: '2026-09-21T00:00:30.000Z',
  executionId: null,
}

function setupStore() {
  setActivePinia(createPinia())
  useAuthStore().user = { username: 'admin', role: 'ADMIN' }
  const store = useVoiceControlStore()
  store.contexts = [context]
  store.selectedRuntimeRef = context.runtimeRef
  store.expectedAlgorithmRunId = context.algorithmRunId
  return store
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}

const binding = { bindingId: '77777777-7777-4777-8777-777777777777', runtimeGeneration: context.runtimeGeneration }
const report: VoicePresentationReportRequest = {
  ...binding, kind: 'FRAME_APPLIED', executionId: execution().executionId,
  requestId: '99999999-9999-4999-8999-999999999999', sequence: 1, frameSequence: 12, applied: true,
}

describe('voiceControl store polling and recovery', () => {
  it('waits for a registered run without querying or deleting saved recovery evidence', async () => {
    const store = setupStore()
    store.error = 'old context error'
    store.errorCode = 'RUNTIME_CONTEXT_NOT_FOUND'
    const keys = [store.activeKey(), store.journalKey(), store.journalKey(true)]
    keys.forEach(key => localStorage.setItem(key, 'preserve-original'))
    await store.selectAlgorithmRun('')
    await store.refreshContexts()
    await store.recover()
    expect(voiceApi.fetchVoiceContexts).not.toHaveBeenCalled()
    expect(store.context).toBeNull()
    expect(store.error).toBe('')
    keys.forEach(key => expect(localStorage.getItem(key)).toBe('preserve-original'))
    await store.selectAlgorithmRun(context.algorithmRunId)
    expect(store.context?.algorithmRunId).toBe(context.algorithmRunId)
    keys.forEach(key => expect(localStorage.getItem(key)).toBe('preserve-original'))
    expect(voiceApi.confirmVoiceProposal).not.toHaveBeenCalled()
  })

  it('ignores a late context lookup after returning to an unprepared scene', async () => {
    const store = setupStore()
    const result = deferred<VoiceRuntimeContext[]>()
    voiceApi.fetchVoiceContexts.mockReturnValueOnce(result.promise)
    const pending = store.refreshContexts()
    await store.selectAlgorithmRun('')
    result.resolve([context])
    expect(await pending).toBe(false)
    expect(store.context).toBeNull()
    expect(store.expectedAlgorithmRunId).toBe('')
    expect(store.error).toBe('')
  })

  it('still reports a genuinely missing registered runtime', async () => {
    const store = setupStore()
    voiceApi.fetchVoiceContexts.mockResolvedValueOnce([])
    expect(await store.refreshContexts()).toBe(false)
    expect(store.errorCode).toBe('RUNTIME_CONTEXT_NOT_FOUND')
    expect(store.error).toContain(context.algorithmRunId)
  })

  it('persists interpretation source and preserves it during recovery', async () => {
    localStorage.clear()
    const store = setupStore()
    const source = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
    voiceApi.createVoiceProposal.mockRejectedValueOnce(new ApiClientError('lost'))
    await store.propose('MISSION_START', source)
    const first = voiceApi.createVoiceProposal.mock.calls[0]!
    const journal = store.loadJournal()!
    expect(journal.interpretationId).toBe(source)
    voiceApi.createVoiceProposal.mockResolvedValueOnce(proposal)
    await store.replayJournal(journal)
    expect(voiceApi.createVoiceProposal).toHaveBeenLastCalledWith(first[0], first[1], source)
    localStorage.clear()
  })
  beforeEach(() => {
    vi.resetAllMocks()
    localStorage.clear()
    voiceApi.fetchVoiceContexts.mockResolvedValue([context])
  })

  describe.each(['propose', 'confirm', 'cancel'] as const)('%s request isolation', action => {
    it('refuses to clear an in-flight request without invalidating its receipt', async () => {
      const store = setupStore()
      store.proposal = proposal
      const result = deferred<unknown>()
      const api = action === 'propose' ? voiceApi.createVoiceProposal
        : action === 'confirm' ? voiceApi.confirmVoiceProposal : voiceApi.cancelVoiceProposal
      api.mockReturnValueOnce(result.promise)
      const pending = action === 'propose' ? store.propose('MISSION_START') : store[action]()
      const original = localStorage.getItem(store.journalKey())
      const version = store.recoveryRequestVersion
      expect(store.clearActive()).toBe(false)
      expect(store.loading).toBe(true)
      expect(store.recoveryRequestVersion).toBe(version)
      expect(localStorage.getItem(store.journalKey())).toBe(original)
      result.resolve(action === 'confirm' ? { proposal: { ...proposal, status: 'CONFIRMED' }, execution: execution() }
        : { ...proposal, status: action === 'cancel' ? 'CANCELLED' : 'AWAITING_CONFIRMATION' })
      await pending
      expect(store.proposal?.status).toBe(action === 'propose' ? 'AWAITING_CONFIRMATION' : action === 'confirm' ? 'CONFIRMED' : 'CANCELLED')
      expect(store.loadJournal()?.phase).toBe('RESOURCE_RECEIVED')
    })

    it.each(['account', 'generation', 'invalidated'] as const)('ignores late success and preserves current state after %s changes', async change => {
      const store = setupStore()
      store.proposal = proposal
      const result = deferred<unknown>()
      const api = action === 'propose' ? voiceApi.createVoiceProposal
        : action === 'confirm' ? voiceApi.confirmVoiceProposal : voiceApi.cancelVoiceProposal
      api.mockReturnValueOnce(result.promise)
      const pending = action === 'propose' ? store.propose('MISSION_START') : store[action]()
      const original = localStorage.getItem(store.journalKey())
      if (change === 'account') useAuthStore().user = { username: 'second-user', role: 'ADMIN' }
      if (change === 'generation') store.contexts = [{ ...context, runtimeGeneration: 'new-generation' }]
      if (change === 'invalidated') store.invalidateRequests()
      store.proposal = null
      store.error = 'current message'
      result.resolve(action === 'confirm' ? { proposal: { ...proposal, status: 'CONFIRMED' }, execution: execution() }
        : { ...proposal, status: action === 'cancel' ? 'CANCELLED' : 'AWAITING_CONFIRMATION' })
      await pending
      expect(store.proposal).toBeNull()
      expect(store.execution).toBeNull()
      expect(store.error).toBe('current message')
      expect(store.loading).toBe(false)
      expect(localStorage.getItem('voice-p0.operation-journal.v1:admin')).toBe(original)
      expect(localStorage.getItem(store.activeKey())).toBeNull()
    })

    it('ignores a late error without marking another account request as unknown', async () => {
      const store = setupStore()
      store.proposal = proposal
      const result = deferred<unknown>()
      const api = action === 'propose' ? voiceApi.createVoiceProposal
        : action === 'confirm' ? voiceApi.confirmVoiceProposal : voiceApi.cancelVoiceProposal
      api.mockReturnValueOnce(result.promise)
      const pending = action === 'propose' ? store.propose('MISSION_START') : store[action]()
      const original = localStorage.getItem(store.journalKey())
      useAuthStore().user = { username: 'second-user', role: 'ADMIN' }
      store.error = 'current message'
      result.reject(new ApiClientError('old request failed'))
      await pending
      expect(store.error).toBe('current message')
      expect(store.responseUnknown).toBe(false)
      expect(localStorage.getItem('voice-p0.operation-journal.v1:admin')).toBe(original)
      expect(localStorage.getItem(store.journalKey())).toBeNull()
    })

    it('still accepts the response when its scope is unchanged', async () => {
      const store = setupStore()
      store.proposal = proposal
      voiceApi.createVoiceProposal.mockResolvedValueOnce(proposal)
      voiceApi.confirmVoiceProposal.mockResolvedValueOnce({ proposal: { ...proposal, status: 'CONFIRMED' }, execution: execution() })
      voiceApi.cancelVoiceProposal.mockResolvedValueOnce({ ...proposal, status: 'CANCELLED' })
      if (action === 'propose') await store.propose('MISSION_START')
      else await store[action]()
      expect(store.proposal?.status).toBe(action === 'propose' ? 'AWAITING_CONFIRMATION' : action === 'confirm' ? 'CONFIRMED' : 'CANCELLED')
      expect(store.loadJournal()?.phase).toBe('RESOURCE_RECEIVED')
      expect(store.loading).toBe(false)
    })
  })

  describe.each([false, true])('recovery storage integrity (presentation=%s)', presentation => {
    it.each(['', '{broken', 'null', '[]', '{}', 'wrong-user', 'missing-key', 'unknown-phase', 'invalid-body'])(
      'preserves %s records and blocks both new writes and replay', async corruption => {
        const store = setupStore()
        if (presentation) store.beginJournal('PRESENTATION_REPORT', '/reports', report, report.requestId)
        else store.beginJournal('CONFIRM', '/confirm', { expectedPlanVersion: 1, expectedPlanHash: proposal.planHash }, proposal.proposalId)
        const original = JSON.parse(localStorage.getItem(store.journalKey(presentation))!)
        const variants: Record<string, unknown> = {
          'wrong-user': { ...original, userScope: 'another-user' },
          'missing-key': { ...original, idempotencyKey: '' },
          'unknown-phase': { ...original, phase: 'UNKNOWN' },
          'invalid-body': { ...original, body: null },
        }
        const raw = corruption in variants ? JSON.stringify(variants[corruption]) : corruption
        localStorage.setItem(store.journalKey(presentation), raw)
        if (presentation) expect(await store.submitPresentationReport(report)).toBe(false)
        else await store.propose('MISSION_START')
        expect(store.recoveryAvailable).toBe(false)
        expect(store.errorCode).toBe('LOCAL_RECOVERY_CORRUPT')
        expect(localStorage.getItem(store.journalKey(presentation))).toBe(raw)
        await store.recover()
        expect(store.responseUnknown).toBe(true)
        expect(store.recoveryPending).toBe(false)
        expect(store.recoveryAvailable).toBe(false)
        expect(localStorage.getItem(store.journalKey(presentation))).toBe(raw)
        expect(voiceApi.createVoiceProposal).not.toHaveBeenCalled()
        expect(voiceApi.confirmVoiceProposal).not.toHaveBeenCalled()
        expect(voiceApi.reportVoicePresentation).not.toHaveBeenCalled()
      })
  })

  it('does not treat denied journal reads as absence, and can reconcile after access returns', async () => {
    const store = setupStore()
    const journal = store.beginJournal('CONFIRM', '/confirm', { expectedPlanVersion: 1, expectedPlanHash: proposal.planHash }, proposal.proposalId)!
    const original = localStorage.getItem(store.journalKey())
    const readFailure = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied') })
    try {
      await store.propose('MISSION_START')
      await store.recover()
      expect(store.recoveryAvailable).toBe(false)
      expect(store.errorCode).toBe('LOCAL_RECOVERY_UNAVAILABLE')
      expect(voiceApi.createVoiceProposal).not.toHaveBeenCalled()
      expect(voiceApi.confirmVoiceProposal).not.toHaveBeenCalled()
    } finally { readFailure.mockRestore() }
    expect(localStorage.getItem(store.journalKey())).toBe(original)
    voiceApi.fetchVoiceProposal.mockResolvedValue({ ...proposal, status: 'CONFIRMED', executionId: execution().executionId })
    voiceApi.fetchVoiceExecution.mockResolvedValue(execution({ presentationStatus: 'REPORTED_APPLIED' }))
    await store.recover()
    expect(store.recoveryAvailable).toBe(true)
    expect(store.responseUnknown).toBe(false)
    expect(store.execution?.executionId).toBe(execution().executionId)
    expect(store.loadJournal()?.idempotencyKey).toBe(journal.idempotencyKey)
    expect(voiceApi.confirmVoiceProposal).not.toHaveBeenCalled()
  })

  it('does not overwrite an old-generation journal when removal is denied', async () => {
    const store = setupStore()
    store.beginJournal('CONFIRM', '/confirm', { expectedPlanVersion: 1, expectedPlanHash: proposal.planHash }, proposal.proposalId)
    const original = localStorage.getItem(store.journalKey())
    store.contexts = [{ ...context, runtimeGeneration: 'next-generation' }]
    const removeFailure = vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('denied') })
    try {
      await store.propose('MISSION_START')
      expect(voiceApi.createVoiceProposal).not.toHaveBeenCalled()
      expect(store.errorCode).toBe('LOCAL_RECOVERY_UNAVAILABLE')
      expect(localStorage.getItem(store.journalKey())).toBe(original)
    } finally { removeFailure.mockRestore() }
  })

  it('still blocks dispatch if reads recover but saving the new journal fails', async () => {
    const store = setupStore()
    store.recoveryAvailable = false
    await store.recover()
    expect(store.recoveryAvailable).toBe(true)
    const writeFailure = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota exceeded') })
    try {
      await store.propose('MISSION_START')
      expect(voiceApi.createVoiceProposal).not.toHaveBeenCalled()
      expect(store.recoveryAvailable).toBe(false)
      expect(store.errorCode).toBe('LOCAL_RECOVERY_UNAVAILABLE')
    } finally { writeFailure.mockRestore() }
  })

  it.each(['QUEUED', 'DISPATCHED', 'ACCEPTED', 'EXECUTING', 'TIMED_OUT'] as const)('retains the %s execution for continued reconciliation', state => {
    const store = setupStore()
    store.execution = execution({ state, outcome: 'UNKNOWN' })
    store.persist()
    const active = localStorage.getItem(store.activeKey())
    expect(store.clearActive()).toBe(false)
    expect(store.execution?.state).toBe(state)
    expect(localStorage.getItem(store.activeKey())).toBe(active)
  })

  it('retains algorithm success while Unity presentation is still pending', () => {
    const store = setupStore()
    store.execution = execution()
    expect(store.clearActive()).toBe(false)
    expect(store.execution?.presentationStatus).toBe('PENDING')
  })

  it.each(['AWAITING_CONFIRMATION', 'CONFIRMED'] as const)('does not hide a %s proposal without its settled execution', status => {
    const store = setupStore()
    store.proposal = { ...proposal, status }
    expect(store.clearActive()).toBe(false)
    expect(store.proposal?.status).toBe(status)
  })

  it.each([false, true])('retains unresolved recovery journal (presentation=%s) even when flags are stale', presentation => {
    const store = setupStore()
    store.execution = execution({ presentationStatus: 'NOT_REQUIRED' })
    if (presentation) store.beginJournal('PRESENTATION_REPORT', '/reports', report, report.requestId)
    else store.beginJournal('CONFIRM', '/confirm', { expectedPlanVersion: 1, expectedPlanHash: proposal.planHash }, proposal.proposalId)
    const original = localStorage.getItem(store.journalKey(presentation))
    expect(store.responseUnknown).toBe(false)
    expect(store.clearActive()).toBe(false)
    expect(localStorage.getItem(store.journalKey(presentation))).toBe(original)
    expect(store.execution).not.toBeNull()
  })

  it('cannot use clear view to bypass an unknown command result with a new key', async () => {
    const store = setupStore()
    voiceApi.createVoiceProposal.mockRejectedValueOnce(new ApiClientError('offline'))
    await store.propose('MISSION_START')
    const original = localStorage.getItem(store.journalKey())
    expect(store.clearActive()).toBe(false)
    await store.propose('MISSION_START')
    expect(voiceApi.createVoiceProposal).toHaveBeenCalledOnce()
    expect(localStorage.getItem(store.journalKey())).toBe(original)
    expect(store.responseUnknown).toBe(true)
  })

  it('clears a settled receipt without deleting journals or submitting cancellation', () => {
    const store = setupStore()
    store.execution = execution({ presentationStatus: 'REPORTED_APPLIED' })
    store.proposal = { ...proposal, status: 'CONFIRMED' }
    store.beginJournal('CONFIRM', '/confirm', { expectedPlanVersion: 1, expectedPlanHash: proposal.planHash }, proposal.proposalId)
    store.updateJournal({ phase: 'RESOURCE_RECEIVED' })
    store.beginJournal('PRESENTATION_REPORT', '/reports', report, report.requestId)
    store.updateJournal({ phase: 'RESOURCE_RECEIVED' }, true)
    store.persist()
    const control = localStorage.getItem(store.journalKey())
    const presentation = localStorage.getItem(store.journalKey(true))
    expect(store.clearActive()).toBe(true)
    expect(store.execution).toBeNull()
    expect(store.proposal).toBeNull()
    expect(localStorage.getItem(store.activeKey())).toBeNull()
    expect(localStorage.getItem(store.journalKey())).toBe(control)
    expect(localStorage.getItem(store.journalKey(true))).toBe(presentation)
    expect(voiceApi.cancelVoiceProposal).not.toHaveBeenCalled()
    expect(voiceApi.confirmVoiceProposal).not.toHaveBeenCalled()
  })

  it.each(['{broken', 'null', '{"phase":"UNRECOGNIZED"}'])('preserves unreadable recovery data: %s', raw => {
    const store = setupStore()
    store.execution = execution({ presentationStatus: 'NOT_REQUIRED' })
    localStorage.setItem(store.journalKey(), raw)
    expect(store.clearActive()).toBe(false)
    expect(store.execution).not.toBeNull()
    expect(localStorage.getItem(store.journalKey())).toBe(raw)
  })

  it.each(['getItem', 'removeItem'] as const)('retains the receipt when local storage %s fails', method => {
    const store = setupStore()
    store.execution = execution({ presentationStatus: 'NOT_REQUIRED' })
    store.persist()
    const original = localStorage.getItem(store.activeKey())
    const storageFailure = vi.spyOn(Storage.prototype, method).mockImplementation(() => { throw new Error('storage denied') })
    try {
      expect(store.clearActive()).toBe(false)
      expect(store.execution).not.toBeNull()
    } finally { storageFailure.mockRestore() }
    expect(localStorage.getItem(store.activeKey())).toBe(original)
  })

  describe.each(['binding', 'challenge', 'report'] as const)('presentation %s isolation', action => {
    it.each(['success', 'error'] as const)('ignores late %s after switching generations', async outcome => {
      const store = setupStore()
      store.presentationBinding = binding
      const result = deferred<unknown>()
      voiceApi.fetchVoicePresentationBinding.mockResolvedValueOnce(binding)
      const api = action === 'binding' ? voiceApi.replaceVoicePresentationBinding
        : action === 'challenge' ? voiceApi.createVoicePresentationChallenge : voiceApi.reportVoicePresentation
      api.mockReturnValueOnce(result.promise)
      const pending = action === 'binding' ? store.takePresentationBinding()
        : action === 'challenge' ? store.requestPresentationChallenge('SCENE_READY', null) : store.submitPresentationReport(report)
      await vi.waitFor(() => expect(api).toHaveBeenCalledOnce())
      const original = localStorage.getItem(store.journalKey(true))
      store.contexts = [{ ...context, runtimeGeneration: 'new-generation' }]
      store.presentationBinding = null
      store.error = 'current message'
      if (outcome === 'error') result.reject(new ApiClientError('old presentation failed'))
      else result.resolve(action === 'binding' ? binding : action === 'challenge'
        ? { ...report, expiresAt: proposal.expiresAt } : context)
      await pending
      expect(store.presentationBinding).toBeNull()
      expect(store.presentationChallenge).toBeNull()
      expect(store.context?.runtimeGeneration).toBe('new-generation')
      expect(store.error).toBe('current message')
      expect(localStorage.getItem(store.journalKey(true))).toBe(original)
      expect(voiceApi.fetchVoiceExecution).not.toHaveBeenCalled()
    })
  })

  it('does not replace a binding when its initial GET returns after a run switch', async () => {
    const store = setupStore()
    const result = deferred<typeof binding>()
    voiceApi.fetchVoicePresentationBinding.mockReturnValueOnce(result.promise)
    const pending = store.takePresentationBinding()
    store.expectedAlgorithmRunId = 'new-run'
    result.resolve(binding)
    await pending
    expect(voiceApi.replaceVoicePresentationBinding).not.toHaveBeenCalled()
    expect(store.loadJournal(true)).toBeNull()
  })

  it('does not rewrite an existing presentation journal when only the binding GET fails', async () => {
    const store = setupStore()
    store.beginJournal('PRESENTATION_REPORT', '/presentation/reports', report, report.requestId)
    const original = localStorage.getItem(store.journalKey(true))
    voiceApi.fetchVoicePresentationBinding.mockRejectedValueOnce(new ApiClientError('offline'))
    await store.takePresentationBinding()
    expect(localStorage.getItem(store.journalKey(true))).toBe(original)
  })

  it('keeps an acknowledged report successful when its follow-up receipt query fails', async () => {
    const store = setupStore()
    store.execution = execution()
    voiceApi.reportVoicePresentation.mockResolvedValueOnce(context)
    voiceApi.fetchVoiceExecution.mockRejectedValueOnce(new ApiClientError('receipt unavailable'))
    expect(await store.submitPresentationReport(report)).toBe(true)
    expect(store.loadJournal(true)?.phase).toBe('RESOURCE_RECEIVED')
    expect(store.error).toBe('receipt unavailable')
  })

  it('does not replace a newer execution with the follow-up presentation receipt', async () => {
    const store = setupStore()
    store.execution = execution()
    voiceApi.reportVoicePresentation.mockResolvedValueOnce(context)
    const result = deferred<VoiceExecution>()
    voiceApi.fetchVoiceExecution.mockReturnValueOnce(result.promise)
    const pending = store.submitPresentationReport(report)
    await vi.waitFor(() => expect(voiceApi.fetchVoiceExecution).toHaveBeenCalledOnce())
    store.execution = execution({ executionId: 'new-execution', state: 'EXECUTING' })
    result.resolve(execution({ presentationStatus: 'REPORTED_APPLIED' }))
    expect(await pending).toBe(true)
    expect(store.execution.executionId).toBe('new-execution')
    expect(store.execution.state).toBe('EXECUTING')
  })

  it('keeps loading while an independent binding request is still pending', async () => {
    const store = setupStore()
    const created = deferred<VoiceProposal>()
    const fetched = deferred<typeof binding>()
    voiceApi.createVoiceProposal.mockReturnValueOnce(created.promise)
    voiceApi.fetchVoicePresentationBinding.mockReturnValueOnce(fetched.promise)
    voiceApi.replaceVoicePresentationBinding.mockResolvedValueOnce(binding)
    const commandPending = store.propose('MISSION_START')
    const bindingPending = store.takePresentationBinding()
    created.resolve(proposal)
    await commandPending
    expect(store.loading).toBe(true)
    fetched.resolve(binding)
    await bindingPending
    expect(store.loading).toBe(false)
    expect(store.loadingRequestIds).toEqual([])
  })

  it('does not unlock a new request when an invalidated old request settles', async () => {
    const store = setupStore()
    const oldResult = deferred<VoiceProposal>()
    const newResult = deferred<VoiceProposal>()
    voiceApi.createVoiceProposal.mockReturnValueOnce(oldResult.promise).mockReturnValueOnce(newResult.promise)
    const oldPending = store.propose('MISSION_START')
    const nextContext = { ...context, algorithmRunId: 'new-run', runtimeGeneration: 'new-generation' }
    voiceApi.fetchVoiceContexts.mockResolvedValueOnce([nextContext])
    await store.selectAlgorithmRun('new-run')
    const newPending = store.propose('MISSION_START')
    oldResult.resolve(proposal)
    await oldPending
    expect(store.loading).toBe(true)
    expect(store.proposal).toBeNull()
    newResult.resolve({ ...proposal, plan: { ...proposal.plan, runtimeGeneration: nextContext.runtimeGeneration } })
    await newPending
    expect(store.loading).toBe(false)
    expect(store.proposal?.plan.runtimeGeneration).toBe(nextContext.runtimeGeneration)
  })

  it.each(['network', 'missing'] as const)('preserves original recovery keys when context is %s instead of deleting or replaying them', async failure => {
    const store = setupStore()
    const journal = store.beginJournal('CONFIRM', `/api/voice/commands/${proposal.proposalId}/confirm`,
      { expectedPlanVersion: 1, expectedPlanHash: proposal.planHash }, proposal.proposalId)!
    const saved = localStorage.getItem(store.journalKey())
    store.proposal = proposal
    store.persist()
    const active = localStorage.getItem(store.activeKey())
    if (failure === 'network') voiceApi.fetchVoiceContexts.mockRejectedValueOnce(new ApiClientError('offline'))
    else voiceApi.fetchVoiceContexts.mockResolvedValueOnce([])
    await store.recover()
    expect(store.responseUnknown).toBe(true)
    expect(localStorage.getItem(store.journalKey())).toBe(saved)
    expect(localStorage.getItem(store.activeKey())).toBe(active)
    expect(store.loadJournal()?.idempotencyKey).toBe(journal.idempotencyKey)
    expect(voiceApi.confirmVoiceProposal).not.toHaveBeenCalled()
    expect(voiceApi.createVoiceProposal).not.toHaveBeenCalled()
  })

  it('does not replay confirmation after an account switch during recovery', async () => {
    const store = setupStore()
    store.beginJournal('CONFIRM', `/api/voice/commands/${proposal.proposalId}/confirm`,
      { expectedPlanVersion: 1, expectedPlanHash: proposal.planHash }, proposal.proposalId)
    const original = localStorage.getItem(store.journalKey())
    let finish!: (value: VoiceProposal) => void
    voiceApi.fetchVoiceProposal.mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
    const pending = store.recover()
    await vi.waitFor(() => expect(voiceApi.fetchVoiceProposal).toHaveBeenCalledOnce())
    expect(store.recoveryPending).toBe(true)
    useAuthStore().user = { username: 'second-user', role: 'ADMIN' }
    finish(proposal)
    await pending
    expect(voiceApi.confirmVoiceProposal).not.toHaveBeenCalled()
    expect(store.proposal).toBeNull()
    expect(localStorage.getItem('voice-p0.operation-journal.v1:admin')).toBe(original)
    expect(localStorage.getItem(store.journalKey())).toBeNull()
    expect(store.recoveryPending).toBe(false)
  })

  it('ignores a recovered receipt after its runtime generation changes', async () => {
    const store = setupStore()
    localStorage.setItem(store.activeKey(), JSON.stringify({ executionId: execution().executionId }))
    let finish!: (value: VoiceExecution) => void
    voiceApi.fetchVoiceExecution.mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
    const pending = store.recover()
    await vi.waitFor(() => expect(voiceApi.fetchVoiceExecution).toHaveBeenCalledOnce())
    store.contexts = [{ ...context, runtimeGeneration: 'new-generation' }]
    finish(execution())
    await pending
    expect(store.execution).toBeNull()
    expect(store.recoveryPending).toBe(false)
  })

  it('keeps the newer recovery locked when an older context query finishes', async () => {
    const store = setupStore()
    let finishOld!: (value: VoiceRuntimeContext[]) => void
    let finishNew!: (value: VoiceRuntimeContext[]) => void
    voiceApi.fetchVoiceContexts
      .mockReturnValueOnce(new Promise(resolve => { finishOld = resolve }))
      .mockReturnValueOnce(new Promise(resolve => { finishNew = resolve }))
    const oldRecovery = store.recover()
    const newRecovery = store.recover()
    finishOld([context])
    await oldRecovery
    expect(store.recoveryPending).toBe(true)
    finishNew([context])
    await newRecovery
    expect(store.recoveryPending).toBe(false)
  })

  it('preserves the original journal when an in-flight replay returns after an account switch', async () => {
    const store = setupStore()
    store.beginJournal('CONFIRM', `/api/voice/commands/${proposal.proposalId}/confirm`,
      { expectedPlanVersion: 1, expectedPlanHash: proposal.planHash }, proposal.proposalId)
    const original = localStorage.getItem(store.journalKey())
    voiceApi.fetchVoiceProposal.mockResolvedValueOnce(proposal)
    let finish!: (value: { proposal: VoiceProposal; execution: VoiceExecution }) => void
    voiceApi.confirmVoiceProposal.mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
    const pending = store.recover()
    await vi.waitFor(() => expect(voiceApi.confirmVoiceProposal).toHaveBeenCalledOnce())
    useAuthStore().user = { username: 'second-user', role: 'ADMIN' }
    finish({ proposal: { ...proposal, status: 'CONFIRMED' }, execution: execution() })
    await pending
    expect(store.execution).toBeNull()
    expect(store.proposal).toBeNull()
    expect(localStorage.getItem('voice-p0.operation-journal.v1:admin')).toBe(original)
    expect(localStorage.getItem(store.activeKey())).toBeNull()
    expect(localStorage.getItem(store.journalKey())).toBeNull()
  })

  it.each(['account', 'run', 'generation', 'resource'] as const)('ignores a late execution response after changing %s', async change => {
    const store = setupStore()
    store.execution = execution({ state: 'EXECUTING' })
    const persist = vi.spyOn(store, 'persist')
    let finish!: (result: VoiceExecution) => void
    voiceApi.fetchVoiceExecution.mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
    const pending = store.poll()
    if (change === 'account') useAuthStore().user = { username: 'second-user', role: 'ADMIN' }
    if (change === 'run') store.expectedAlgorithmRunId = 'new-run'
    if (change === 'generation') store.contexts = [{ ...context, runtimeGeneration: 'new-generation' }]
    if (change === 'resource') store.execution = execution({ executionId: 'new-execution', state: 'EXECUTING' })
    finish(execution({ state: 'SUCCEEDED' }))
    await pending
    expect(store.execution.executionId).toBe(change === 'resource' ? 'new-execution' : execution().executionId)
    expect(store.execution.state).toBe('EXECUTING')
    expect(persist).not.toHaveBeenCalled()
    expect(voiceApi.fetchVoiceContexts).not.toHaveBeenCalled()
  })

  it('ignores an old proposal poll and its error after the selected run changes', async () => {
    const store = setupStore()
    store.proposal = proposal
    let reject!: (error: Error) => void
    voiceApi.fetchVoiceProposal.mockReturnValueOnce(new Promise((_resolve, fail) => { reject = fail }))
    const pending = store.poll()
    store.expectedAlgorithmRunId = 'new-run'
    store.proposal = null
    store.error = 'new context message'
    reject(new Error('old poll failed'))
    await pending
    expect(store.proposal).toBeNull()
    expect(store.error).toBe('new context message')
    expect(store.executionPollInFlight).toBe(false)
  })

  it('does not replace a newer confirmation with an older awaiting-confirmation poll', async () => {
    const store = setupStore()
    store.proposal = proposal
    let finish!: (result: VoiceProposal) => void
    voiceApi.fetchVoiceProposal.mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
    const pending = store.poll()
    store.proposal = { ...proposal, status: 'CONFIRMED' }
    store.execution = execution()
    finish(proposal)
    await pending
    expect(store.proposal.status).toBe('CONFIRMED')
  })

  it('rejects an execution payload from another runtime even when the request is still current', async () => {
    const store = setupStore()
    store.execution = execution({ state: 'EXECUTING' })
    voiceApi.fetchVoiceExecution.mockResolvedValueOnce(execution({ runtimeGeneration: 'wrong-generation' }))
    await store.poll()
    expect(store.execution.state).toBe('EXECUTING')
    expect(store.execution.runtimeGeneration).toBe(context.runtimeGeneration)
  })

  it('does not roll back a successful cancellation when an older proposal poll arrives', async () => {
    const store = setupStore()
    store.proposal = proposal
    let finish!: (result: VoiceProposal) => void
    voiceApi.fetchVoiceProposal.mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
    const pending = store.poll()
    store.proposal = { ...proposal, status: 'CANCELLED' }
    finish(proposal)
    await pending
    expect(store.proposal.status).toBe('CANCELLED')
  })

  it('does not regress newer presentation evidence for the same execution ID', async () => {
    const store = setupStore()
    store.execution = execution()
    let finish!: (result: VoiceExecution) => void
    voiceApi.fetchVoiceExecution.mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
    const pending = store.poll()
    store.execution = execution({ presentationStatus: 'REPORTED_APPLIED' })
    finish(execution())
    await pending
    expect(store.execution.presentationStatus).toBe('REPORTED_APPLIED')
  })

  it('continues querying after algorithm success while presentation is PENDING', async () => {
    const store = setupStore()
    store.execution = execution()
    voiceApi.fetchVoiceExecution.mockResolvedValue(execution({ presentationStatus: 'STALE' }))

    await store.poll()

    expect(voiceApi.fetchVoiceExecution).toHaveBeenCalledWith(store.execution.executionId)
    expect(store.execution.presentationStatus).toBe('STALE')
  })

  it('stops querying after presentation becomes REPORTED_APPLIED', async () => {
    const store = setupStore()
    store.execution = execution({ presentationStatus: 'REPORTED_APPLIED' })

    await store.poll()

    expect(voiceApi.fetchVoiceExecution).not.toHaveBeenCalled()
  })

  it('stops querying an algorithm failure terminal state', async () => {
    const store = setupStore()
    store.execution = execution({ state: 'FAILED', outcome: 'FAILED', presentationStatus: 'NOT_REQUIRED' })

    await store.poll()

    expect(voiceApi.fetchVoiceExecution).not.toHaveBeenCalled()
  })

  it('keeps reconciling a TIMED_OUT execution because its result is still unknown', async () => {
    const store = setupStore()
    store.execution = execution({ state: 'TIMED_OUT', outcome: 'UNKNOWN', timedOutAt: '2026-09-21T00:00:02.000Z' })
    voiceApi.fetchVoiceExecution.mockResolvedValue(execution())

    await store.poll()

    expect(voiceApi.fetchVoiceExecution).toHaveBeenCalledOnce()
    expect(store.execution.state).toBe('SUCCEEDED')
  })

  it('restores the same execution from the user-scoped local recovery record', async () => {
    const store = setupStore()
    const savedExecution = execution({ presentationStatus: 'STALE' })
    localStorage.setItem('voice-p0.active-command.v2:admin', JSON.stringify({ executionId: savedExecution.executionId }))
    voiceApi.fetchVoiceExecution.mockResolvedValue(savedExecution)

    await store.recover()

    expect(voiceApi.fetchVoiceExecution).toHaveBeenCalledWith(savedExecution.executionId)
    expect(store.execution).toEqual(savedExecution)
  })

  it('replays an unknown confirmation with the original body and idempotency key', async () => {
    const store = setupStore()
    const idempotencyKey = '66666666-6666-4666-8666-666666666666'
    const body = { expectedPlanVersion: 1 as const, expectedPlanHash: proposal.planHash }
    localStorage.setItem('voice-p0.operation-journal.v1:admin', JSON.stringify({
      userScope: 'admin',
      runtimeRef: context.runtimeRef,
      runtimeGeneration: context.runtimeGeneration,
      kind: 'CONFIRM',
      method: 'POST',
      path: `/api/voice/commands/${proposal.proposalId}/confirm`,
      idempotencyKey,
      body,
      resourceId: proposal.proposalId,
      phase: 'RESPONSE_UNKNOWN',
      updatedAt: '2026-09-21T00:00:02.000Z',
    }))
    voiceApi.fetchVoiceProposal.mockResolvedValue(proposal)
    voiceApi.confirmVoiceProposal.mockResolvedValue({ proposal: { ...proposal, status: 'CONFIRMED' }, execution: execution() })
    voiceApi.fetchVoiceExecution.mockResolvedValue(execution())

    await store.recover()

    expect(voiceApi.confirmVoiceProposal).toHaveBeenCalledWith(proposal.proposalId, body, idempotencyKey)
    expect(store.execution?.executionId).toBe(execution().executionId)
  })

  it('does not clear an unknown control response when presentation binding succeeds', async () => {
    const store = setupStore()
    store.responseUnknown = true
    store.error = '请求结果未知，请重新查询'
    store.errorCode = 'NETWORK_ERROR'
    voiceApi.fetchVoicePresentationBinding.mockResolvedValue({
      bindingId: '77777777-7777-4777-8777-777777777777',
      runtimeGeneration: context.runtimeGeneration,
    })
    voiceApi.replaceVoicePresentationBinding.mockResolvedValue({
      bindingId: '88888888-8888-4888-8888-888888888888',
      runtimeGeneration: context.runtimeGeneration,
    })

    await store.takePresentationBinding()

    expect(store.responseUnknown).toBe(true)
  })

  it('allows an operator command while a presentation request has its own recovery journal', async () => {
    const store = setupStore()
    store.beginJournal('PRESENTATION_CHALLENGE', '/presentation/challenges', {
      runtimeGeneration: context.runtimeGeneration,
      bindingId: '77777777-7777-4777-8777-777777777777',
      kind: 'SCENE_READY',
      executionId: null,
    })
    voiceApi.createVoiceProposal.mockResolvedValue(proposal)

    await store.propose('MISSION_PAUSE')

    expect(voiceApi.createVoiceProposal).toHaveBeenCalledOnce()
    expect(store.responseUnknown).toBe(false)
  })

  it('discards only the expired presentation recovery before a STALE resync', () => {
    const store = setupStore()
    store.execution = execution({ presentationStatus: 'STALE' })
    store.presentationChallenge = {
      requestId: '99999999-9999-4999-8999-999999999999',
      runtimeGeneration: context.runtimeGeneration,
      bindingId: '77777777-7777-4777-8777-777777777777',
      sequence: 4,
      kind: 'FRAME_APPLIED',
      executionId: store.execution.executionId,
      expiresAt: '2026-09-21T00:00:30.000Z',
    }
    localStorage.setItem('voice-p0.presentation-journal.v1:admin', JSON.stringify({
      userScope: 'admin',
      runtimeRef: context.runtimeRef,
      runtimeGeneration: context.runtimeGeneration,
      kind: 'PRESENTATION_REPORT',
      phase: 'RESPONSE_UNKNOWN',
    }))
    localStorage.setItem('voice-p0.operation-journal.v1:admin', JSON.stringify({ kind: 'CONFIRM' }))

    expect(store.clearStalePresentationRecovery()).toBe(true)
    expect(store.presentationChallenge).toBeNull()
    expect(localStorage.getItem('voice-p0.presentation-journal.v1:admin')).toBeNull()
    expect(localStorage.getItem('voice-p0.operation-journal.v1:admin')).not.toBeNull()
  })

  it('does not expose the previous user execution after switching users', async () => {
    const store = setupStore()
    store.execution = execution({ presentationStatus: 'REPORTED_APPLIED' })
    store.proposal = proposal
    useAuthStore().user = { username: 'second-user', role: 'ADMIN' }

    await store.recover()

    expect(store.execution).toBeNull()
    expect(store.proposal).toBeNull()
    expect(voiceApi.fetchVoiceExecution).not.toHaveBeenCalled()
    expect(voiceApi.fetchVoiceProposal).not.toHaveBeenCalled()
  })

  it('ignores a context response arriving after the user changes', async () => {
    const store = setupStore()
    store.contexts = []
    let finish!: (value: VoiceRuntimeContext[]) => void
    voiceApi.fetchVoiceContexts.mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
    const pending = store.refreshContexts()
    useAuthStore().user = { username: 'second-user', role: 'ADMIN' }
    finish([context])

    expect(await pending).toBe(false)
    expect(store.contexts).toEqual([])
  })
})

it.each([[400, 'INVALID_REQUEST'], [409, 'PLAN_MISMATCH']] as const)('I05 %s confirmation rejection is not automatically replayed', async (status, code) => {
  vi.clearAllMocks()
  localStorage.clear()
  const store = setupStore()
  store.proposal = { ...proposal, expiresAt: new Date(Date.now() + 30000).toISOString() }
  voiceApi.fetchVoiceContexts.mockResolvedValue([context])
  voiceApi.confirmVoiceProposal.mockRejectedValue(new ApiClientError('request rejected', status, code))
  await store.confirm()
  expect(store.errorCode).toBe(code)
  expect(store.responseUnknown).toBe(false)
  expect(store.execution).toBeNull()
  const journal = JSON.parse(localStorage.getItem('voice-p0.operation-journal.v1:admin')!)
  expect(journal.phase).toBe('BUSINESS_REJECTED')
  await store.recover()
  expect(voiceApi.confirmVoiceProposal).toHaveBeenCalledTimes(1)
})
