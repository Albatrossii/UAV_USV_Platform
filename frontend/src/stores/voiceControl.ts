import { defineStore } from 'pinia'

import {
  cancelVoiceProposal,
  confirmVoiceProposal,
  createVoiceProposal,
  createVoicePresentationChallenge,
  fetchVoiceContexts,
  fetchVoiceExecution,
  fetchVoicePresentationBinding,
  fetchVoiceProposal,
  replaceVoicePresentationBinding,
  reportVoicePresentation,
} from '@/api/voiceControl'
import { ApiClientError } from '@/api/http'
import { useAuthStore } from '@/stores/auth'
import type {
  VoiceExecution,
  VoiceIntent,
  VoicePlanGuardRequest,
  VoicePresentationBinding,
  VoicePresentationBindingRequest,
  VoicePresentationChallenge,
  VoicePresentationChallengeRequest,
  VoicePresentationReportRequest,
  VoiceProposal,
  VoiceProposalRequest,
  VoiceRuntimeContext,
} from '@/types/voiceControl'

const activeKeyPrefix = 'voice-p0.active-command.v2:'
const journalKeyPrefix = 'voice-p0.operation-journal.v1:'
const presentationJournalKeyPrefix = 'voice-p0.presentation-journal.v1:'
const terminalStates = new Set(['SUCCEEDED', 'REJECTED', 'FAILED', 'INVALIDATED'])

function executionNeedsPolling(execution: VoiceExecution) {
  if (!terminalStates.has(execution.state)) return true
  return execution.state === 'SUCCEEDED' && execution.presentationStatus === 'PENDING'
}

interface RecoveryState { proposalId?: string; executionId?: string }
type JournalKind = 'CREATE_PROPOSAL' | 'CONFIRM' | 'CANCEL' | 'REPLACE_BINDING' | 'PRESENTATION_CHALLENGE' | 'PRESENTATION_REPORT'
const presentationJournalKinds = new Set<JournalKind>(['REPLACE_BINDING', 'PRESENTATION_CHALLENGE', 'PRESENTATION_REPORT'])
type JournalBody = VoiceProposalRequest | VoicePlanGuardRequest | VoicePresentationBindingRequest | VoicePresentationChallengeRequest | VoicePresentationReportRequest
interface OperationJournal {
  interpretationId?: string
  userScope: string
  runtimeRef: string
  runtimeGeneration: string
  kind: JournalKind
  method: 'POST'
  path: string
  idempotencyKey: string
  body: JournalBody
  resourceId: string | null
  executionId?: string | null
  phase: 'PREPARED' | 'RESPONSE_UNKNOWN' | 'RESOURCE_RECEIVED' | 'BUSINESS_REJECTED'
  updatedAt: string
}

function newKey() { return crypto.randomUUID().toLowerCase() }
function isUnknownResult(error: unknown) {
  return !(error instanceof ApiClientError) || error.status === undefined || error.status >= 500
}

export const useVoiceControlStore = defineStore('voiceControl', {
  state: () => ({
    contexts: [] as VoiceRuntimeContext[],
    selectedRuntimeRef: '',
    expectedAlgorithmRunId: '',
    proposal: null as VoiceProposal | null,
    execution: null as VoiceExecution | null,
    presentationBinding: null as VoicePresentationBinding | null,
    presentationChallenge: null as VoicePresentationChallenge | null,
    loading: false,
    error: '',
    errorCode: '',
    recoveryPending: false,
    recoveryAvailable: true,
    responseUnknown: false,
    contextRequestVersion: 0,
    executionPollInFlight: false,
  }),
  getters: {
    context(state): VoiceRuntimeContext | null {
      return state.contexts.find(item => item.runtimeRef === state.selectedRuntimeRef) ?? null
    },
  },
  actions: {
    userScope() { return useAuthStore().user?.username ?? '' },
    activeKey() { return `${activeKeyPrefix}${this.userScope()}` },
    journalKey(presentation = false) {
      return `${presentation ? presentationJournalKeyPrefix : journalKeyPrefix}${this.userScope()}`
    },
    captureError(error: unknown, fallback: string) {
      this.error = error instanceof Error ? error.message : fallback
      this.errorCode = typeof error === 'object' && error && 'code' in error ? String(error.code ?? '') : ''
    },
    saveJournal(journal: OperationJournal) {
      try {
        localStorage.setItem(this.journalKey(presentationJournalKinds.has(journal.kind)), JSON.stringify(journal))
        this.recoveryAvailable = true
        return true
      } catch {
        this.recoveryAvailable = false
        this.errorCode = 'LOCAL_RECOVERY_UNAVAILABLE'
        this.error = '浏览器本地存储不可用，无法保证刷新后的安全恢复，本次控制操作已阻止。'
        return false
      }
    },
    loadJournal(presentation = false): OperationJournal | null {
      try {
        const raw = localStorage.getItem(this.journalKey(presentation))
        if (!raw) return null
        const journal = JSON.parse(raw) as OperationJournal
        return journal.userScope === this.userScope() ? journal : null
      } catch { return null }
    },
    updateJournal(patch: Partial<OperationJournal>, presentation = false) {
      const journal = this.loadJournal(presentation)
      if (journal) this.saveJournal({ ...journal, ...patch, updatedAt: new Date().toISOString() })
    },
    persist() {
      const value: RecoveryState = { proposalId: this.proposal?.proposalId, executionId: this.execution?.executionId }
      try { localStorage.setItem(this.activeKey(), JSON.stringify(value)) } catch { this.recoveryAvailable = false }
    },
    clearActive() {
      this.proposal = null
      this.execution = null
      this.error = ''
      this.errorCode = ''
      this.recoveryPending = false
      this.responseUnknown = false
      try {
        localStorage.removeItem(this.activeKey())
        localStorage.removeItem(this.journalKey())
        localStorage.removeItem(this.journalKey(true))
      } catch { this.recoveryAvailable = false }
    },
    clearStalePresentationRecovery() {
      if (this.execution?.state !== 'SUCCEEDED' || this.execution.presentationStatus !== 'STALE') return false
      this.presentationChallenge = null
      try {
        localStorage.removeItem(this.journalKey(true))
        return true
      } catch {
        this.recoveryAvailable = false
        this.errorCode = 'LOCAL_RECOVERY_UNAVAILABLE'
        this.error = '无法清理已过期的展示恢复记录，请刷新页面后重试。'
        return false
      }
    },
    async refreshContexts(expectedAlgorithmRunId?: string) {
      expectedAlgorithmRunId ??= this.expectedAlgorithmRunId
      const requestedBy = this.userScope()
      const requestVersion = ++this.contextRequestVersion
      try {
        const contexts = await fetchVoiceContexts()
        if (requestedBy !== this.userScope() || requestVersion !== this.contextRequestVersion || expectedAlgorithmRunId !== this.expectedAlgorithmRunId) return false
        this.contexts = contexts
        const matches = contexts.filter(item => item.algorithmRunId === expectedAlgorithmRunId)
        if (matches.length !== 1) {
          this.selectedRuntimeRef = ''
          this.errorCode = matches.length ? 'AMBIGUOUS_RUNTIME_CONTEXT' : 'RUNTIME_CONTEXT_NOT_FOUND'
          this.error = matches.length
            ? `运行 ${expectedAlgorithmRunId} 匹配到多个上下文，请重新查询运行代次。`
            : `未找到算法运行 ${expectedAlgorithmRunId} 对应的控制上下文。`
          return false
        }
        this.selectedRuntimeRef = matches[0]!.runtimeRef
        this.error = ''
        this.errorCode = ''
        return true
      } catch (error) {
        if (requestedBy === this.userScope() && requestVersion === this.contextRequestVersion) this.captureError(error, '无法读取语音控制上下文')
        return false
      }
    },
    async selectAlgorithmRun(algorithmRunId: string) {
      if (this.expectedAlgorithmRunId !== algorithmRunId) {
        this.expectedAlgorithmRunId = algorithmRunId
        this.selectedRuntimeRef = ''
        // A Pinia store survives scenario regeneration. Never carry an old
        // proposal/execution into the newly selected algorithm runtime: its
        // presentation challenge belongs to the old runtime generation and
        // can keep the new scene stuck in automatic resynchronization.
        this.proposal = null
        this.execution = null
        this.presentationBinding = null
        this.presentationChallenge = null
        this.responseUnknown = false
        this.recoveryPending = false
        this.error = ''
        this.errorCode = ''
        try { localStorage.removeItem(this.activeKey()) } catch { this.recoveryAvailable = false }
      }
      return this.refreshContexts(algorithmRunId)
    },
    async recover() {
      if (!this.userScope()) return
      // Pinia survives route changes. Clear volatile data before reading the
      // current user's scoped records so another user's execution can never
      // remain visible after logout/login in the same browser tab.
      this.proposal = null
      this.execution = null
      this.presentationBinding = null
      this.presentationChallenge = null
      this.responseUnknown = false
      try {
        // v1 was not user-scoped and therefore must not be trusted or exposed
        // after upgrading to the v3.0 recovery rules.
        localStorage.removeItem('voice-p0.active-command.v1')
      } catch { this.recoveryAvailable = false }
      await this.refreshContexts()
      let journal = this.loadJournal()
      if (journal && (!this.context
        || journal.runtimeRef !== this.context.runtimeRef
        || journal.runtimeGeneration !== this.context.runtimeGeneration)) {
        // Do not replay an unknown-result write against a different runtime.
        try { localStorage.removeItem(this.journalKey()) } catch { this.recoveryAvailable = false }
        journal = null
      }
      if (journal && presentationJournalKinds.has(journal.kind)) {
        this.saveJournal(journal)
        try { localStorage.removeItem(this.journalKey()) } catch { this.recoveryAvailable = false }
        journal = null
      }
      if (journal && ['PREPARED', 'RESPONSE_UNKNOWN'].includes(journal.phase)) {
        this.recoveryPending = true
        try {
          await this.replayJournal(journal)
          this.responseUnknown = false
        } catch (error) {
          this.captureError(error, '上次请求结果仍待确认')
          this.responseUnknown = isUnknownResult(error)
          if (!this.responseUnknown) this.updateJournal({ phase: 'BUSINESS_REJECTED' })
        } finally { this.recoveryPending = false }
      } else if (journal) {
        this.responseUnknown = false
      }
      let presentationJournal = this.loadJournal(true)
      if (presentationJournal && (!this.context
        || presentationJournal.runtimeRef !== this.context.runtimeRef
        || presentationJournal.runtimeGeneration !== this.context.runtimeGeneration)) {
        try { localStorage.removeItem(this.journalKey(true)) } catch { this.recoveryAvailable = false }
        presentationJournal = null
      }
      if (presentationJournal && ['PREPARED', 'RESPONSE_UNKNOWN'].includes(presentationJournal.phase)) {
        try {
          await this.replayJournal(presentationJournal)
        } catch {
          // Presentation recovery remains isolated from operator commands.
          // Keep its original key for the next recovery attempt.
        }
      }
      try {
        const raw = localStorage.getItem(this.activeKey())
        if (!raw) return
        const saved = JSON.parse(raw) as RecoveryState
        if (saved.proposalId) {
          const recoveredProposal = await fetchVoiceProposal(saved.proposalId)
          if (this.context
            && recoveredProposal.plan.runtimeRef === this.context.runtimeRef
            && recoveredProposal.plan.runtimeGeneration === this.context.runtimeGeneration) {
            this.proposal = recoveredProposal
          }
        }
        if (saved.executionId) {
          const recoveredExecution = await fetchVoiceExecution(saved.executionId)
          if (this.context
            && recoveredExecution.runtimeRef === this.context.runtimeRef
            && recoveredExecution.runtimeGeneration === this.context.runtimeGeneration) {
            this.execution = recoveredExecution
          }
        }
        if ((!this.proposal && !this.execution) && (saved.proposalId || saved.executionId)) {
          localStorage.removeItem(this.activeKey())
        }
      } catch (error) { this.captureError(error, '恢复上次指令状态失败') }
    },
    async replayJournal(journal: OperationJournal) {
      const presentation = presentationJournalKinds.has(journal.kind)
      if (journal.kind === 'CREATE_PROPOSAL') {
        this.proposal = await createVoiceProposal(journal.body as VoiceProposalRequest, journal.idempotencyKey, journal.interpretationId)
        this.execution = null
        this.updateJournal({ resourceId: this.proposal.proposalId, phase: 'RESOURCE_RECEIVED' }, presentation)
      } else if (journal.kind === 'CONFIRM') {
        const known = await fetchVoiceProposal(journal.resourceId ?? '')
        if (known.status === 'CONFIRMED' && known.executionId) {
          this.proposal = known
          this.execution = await fetchVoiceExecution(known.executionId)
        } else {
          const result = await confirmVoiceProposal(known.proposalId, journal.body as VoicePlanGuardRequest, journal.idempotencyKey)
          this.proposal = result.proposal
          this.execution = result.execution
        }
        this.updateJournal({ executionId: this.execution.executionId, phase: 'RESOURCE_RECEIVED' }, presentation)
      } else if (journal.kind === 'CANCEL') {
        const known = await fetchVoiceProposal(journal.resourceId ?? '')
        this.proposal = known.status === 'AWAITING_CONFIRMATION'
          ? await cancelVoiceProposal(known.proposalId, journal.body as VoicePlanGuardRequest, journal.idempotencyKey)
          : known
        this.updateJournal({ phase: 'RESOURCE_RECEIVED' })
      } else if (journal.kind === 'REPLACE_BINDING') {
        this.presentationBinding = await fetchVoicePresentationBinding(journal.runtimeRef)
        if (this.presentationBinding.bindingId === (journal.body as VoicePresentationBindingRequest).expectedBindingId) {
          this.presentationBinding = await replaceVoicePresentationBinding(journal.runtimeRef, journal.body as VoicePresentationBindingRequest, journal.idempotencyKey)
        }
        this.updateJournal({ resourceId: this.presentationBinding.bindingId, phase: 'RESOURCE_RECEIVED' }, presentation)
      } else if (journal.kind === 'PRESENTATION_CHALLENGE') {
        this.presentationChallenge = await createVoicePresentationChallenge(
          journal.runtimeRef,
          journal.body as VoicePresentationChallengeRequest,
          journal.idempotencyKey,
        )
        this.updateJournal({ resourceId: this.presentationChallenge.requestId, phase: 'RESOURCE_RECEIVED' }, presentation)
      } else if (journal.kind === 'PRESENTATION_REPORT') {
        const updated = await reportVoicePresentation(
          journal.runtimeRef,
          journal.body as VoicePresentationReportRequest,
          journal.idempotencyKey,
        )
        this.contexts = this.contexts.map(item => item.runtimeRef === updated.runtimeRef ? updated : item)
        this.updateJournal({ resourceId: (journal.body as VoicePresentationReportRequest).requestId, phase: 'RESOURCE_RECEIVED' }, presentation)
      }
      this.persist()
    },
    beginJournal(kind: JournalKind, path: string, body: JournalBody, resourceId: string | null = null, interpretationId?: string) {
      if (!this.context || !this.userScope()) return null
      const presentation = presentationJournalKinds.has(kind)
      let previous = this.loadJournal(presentation)
      if (previous
        && (previous.runtimeRef !== this.context.runtimeRef
          || previous.runtimeGeneration !== this.context.runtimeGeneration)) {
        try { localStorage.removeItem(this.journalKey(presentation)) } catch { this.recoveryAvailable = false }
        previous = null
      }
      if (previous && ['PREPARED', 'RESPONSE_UNKNOWN'].includes(previous.phase)) {
        if (!presentation) {
          this.responseUnknown = true
          this.errorCode = 'RESPONSE_UNKNOWN'
          this.error = '上一次写请求的结果仍待确认，请先使用原幂等键恢复，不能创建新请求。'
        }
        return null
      }
      const journal: OperationJournal = {
        userScope: this.userScope(), runtimeRef: this.context.runtimeRef,
        runtimeGeneration: this.context.runtimeGeneration, kind, method: 'POST', path,
        idempotencyKey: newKey(), body, resourceId, phase: 'PREPARED', updatedAt: new Date().toISOString(),
        ...(interpretationId ? { interpretationId } : {}),
      }
      return this.saveJournal(journal) ? journal : null
    },
    async propose(intent: VoiceIntent, interpretationId?: string) {
      this.loading = true
      this.error = ''
      try {
        // The server validates this cached context against the authoritative runtime,
        // generation, context version, state and heartbeat before persisting a proposal.
        // Avoid a serial GET immediately before the proposal POST on the voice path.
        if (!this.context || this.context.algorithmRunId !== this.expectedAlgorithmRunId) {
          this.errorCode = 'RUNTIME_CONTEXT_NOT_FOUND'
          this.error = '没有当前算法运行对应的控制上下文，请刷新运行状态后重试。'
          return
        }
        const body: VoiceProposalRequest = {
          runtimeRef: this.context.runtimeRef, runtimeGeneration: this.context.runtimeGeneration,
          expectedContextVersion: this.context.contextVersion, intent,
        }
        const journal = this.beginJournal('CREATE_PROPOSAL', '/api/voice/commands/proposals', body, null, interpretationId)
        if (!journal) return
        try { this.proposal = await createVoiceProposal(body, journal.idempotencyKey, journal.interpretationId) }
        catch (error) {
          this.responseUnknown = isUnknownResult(error)
          this.updateJournal({ phase: this.responseUnknown ? 'RESPONSE_UNKNOWN' : 'BUSINESS_REJECTED' })
          throw error
        }
        this.execution = null
        this.updateJournal({ resourceId: this.proposal.proposalId, phase: 'RESOURCE_RECEIVED' })
        this.responseUnknown = false
        this.persist()
      } catch (error) { this.captureError(error, '创建指令提案失败') }
      finally { this.loading = false }
    },
    async confirm() {
      if (!this.proposal || !this.context) return
      this.loading = true
      const body: VoicePlanGuardRequest = { expectedPlanVersion: this.proposal.planVersion, expectedPlanHash: this.proposal.planHash }
      const journal = this.beginJournal('CONFIRM', `/api/voice/commands/${this.proposal.proposalId}/confirm`, body, this.proposal.proposalId)
      if (!journal) { this.loading = false; return }
      try {
        const result = await confirmVoiceProposal(this.proposal.proposalId, body, journal.idempotencyKey)
        this.proposal = result.proposal
        this.execution = result.execution
        this.error = ''
        this.updateJournal({ executionId: result.execution.executionId, phase: 'RESOURCE_RECEIVED' })
        this.persist()
      } catch (error) {
        this.responseUnknown = isUnknownResult(error)
        this.updateJournal({ phase: this.responseUnknown ? 'RESPONSE_UNKNOWN' : 'BUSINESS_REJECTED' })
        this.captureError(error, '确认指令失败')
      } finally { this.loading = false }
    },
    async cancel() {
      if (!this.proposal || !this.context) return
      this.loading = true
      const body: VoicePlanGuardRequest = { expectedPlanVersion: this.proposal.planVersion, expectedPlanHash: this.proposal.planHash }
      const journal = this.beginJournal('CANCEL', `/api/voice/commands/${this.proposal.proposalId}/cancel`, body, this.proposal.proposalId)
      if (!journal) { this.loading = false; return }
      try {
        this.proposal = await cancelVoiceProposal(this.proposal.proposalId, body, journal.idempotencyKey)
        this.error = ''
        this.updateJournal({ phase: 'RESOURCE_RECEIVED' })
        this.persist()
      } catch (error) {
        this.responseUnknown = isUnknownResult(error)
        this.updateJournal({ phase: this.responseUnknown ? 'RESPONSE_UNKNOWN' : 'BUSINESS_REJECTED' })
        this.captureError(error, '取消指令失败')
      } finally { this.loading = false }
    },
    async takePresentationBinding() {
      if (!this.context) return
      this.loading = true
      try {
        const current = await fetchVoicePresentationBinding(this.context.runtimeRef)
        const body: VoicePresentationBindingRequest = { runtimeGeneration: this.context.runtimeGeneration, expectedBindingId: current.bindingId }
        const journal = this.beginJournal('REPLACE_BINDING', `/api/voice/contexts/${this.context.runtimeRef}/presentation/bindings`, body)
        if (!journal) return
        this.presentationBinding = await replaceVoicePresentationBinding(this.context.runtimeRef, body, journal.idempotencyKey)
        this.updateJournal({ resourceId: this.presentationBinding.bindingId, phase: 'RESOURCE_RECEIVED' }, true)
        this.error = ''
        this.errorCode = ''
      } catch (error) {
        const unknown = isUnknownResult(error)
        this.updateJournal({ phase: unknown ? 'RESPONSE_UNKNOWN' : 'BUSINESS_REJECTED' }, true)
        this.captureError(error, '建立 Unity 展示绑定失败')
      } finally { this.loading = false }
    },
    async requestPresentationChallenge(kind: 'SCENE_READY' | 'FRAME_APPLIED', executionId: string | null) {
      if (!this.context || !this.presentationBinding?.bindingId) return null
      const body: VoicePresentationChallengeRequest = {
        runtimeGeneration: this.context.runtimeGeneration,
        bindingId: this.presentationBinding.bindingId,
        kind,
        executionId,
      }
      const journal = this.beginJournal(
        'PRESENTATION_CHALLENGE',
        `/api/voice/contexts/${this.context.runtimeRef}/presentation/challenges`,
        body,
      )
      if (!journal) return null
      try {
        this.presentationChallenge = await createVoicePresentationChallenge(this.context.runtimeRef, body, journal.idempotencyKey)
        this.updateJournal({ resourceId: this.presentationChallenge.requestId, phase: 'RESOURCE_RECEIVED' }, true)
        this.error = ''
        this.errorCode = ''
        return this.presentationChallenge
      } catch (error) {
        const unknown = isUnknownResult(error)
        this.updateJournal({ phase: unknown ? 'RESPONSE_UNKNOWN' : 'BUSINESS_REJECTED' }, true)
        this.captureError(error, '申请 Unity 展示挑战失败')
        return null
      }
    },
    async submitPresentationReport(body: VoicePresentationReportRequest) {
      if (!this.context) return false
      const journal = this.beginJournal(
        'PRESENTATION_REPORT',
        `/api/voice/contexts/${this.context.runtimeRef}/presentation/reports`,
        body,
        body.requestId,
      )
      if (!journal) return false
      try {
        const updated = await reportVoicePresentation(this.context.runtimeRef, body, journal.idempotencyKey)
        this.contexts = this.contexts.map(item => item.runtimeRef === updated.runtimeRef ? updated : item)
        this.presentationChallenge = null
        this.updateJournal({ phase: 'RESOURCE_RECEIVED' }, true)
        this.error = ''
        this.errorCode = ''
        if (body.kind === 'FRAME_APPLIED' && this.execution) {
          this.execution = await fetchVoiceExecution(this.execution.executionId)
          if (terminalStates.has(this.execution.state)) await this.refreshContexts()
          this.persist()
        }
        return true
      } catch (error) {
        const unknown = isUnknownResult(error)
        this.updateJournal({ phase: unknown ? 'RESPONSE_UNKNOWN' : 'BUSINESS_REJECTED' }, true)
        this.captureError(error, '提交 Unity 展示报告失败')
        return false
      }
    },
    async poll() {
      if (this.executionPollInFlight) return
      this.executionPollInFlight = true
      try {
        if (this.execution) {
          // Algorithm success and Unity presentation completion are separate.
          // Keep reading while the backend is still resolving PENDING to
          // REPORTED_APPLIED or STALE.
          if (!executionNeedsPolling(this.execution)) return
          this.execution = await fetchVoiceExecution(this.execution.executionId)
          if (terminalStates.has(this.execution.state)) await this.refreshContexts()
          this.persist()
        } else if (this.proposal?.status === 'AWAITING_CONFIRMATION') {
          this.proposal = await fetchVoiceProposal(this.proposal.proposalId)
          this.persist()
        }
      } catch (error) { this.captureError(error, '刷新指令状态失败') }
      finally { this.executionPollInFlight = false }
    },
  },
})
