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
type JournalRead = { ok: true; journal: OperationJournal | null } | { ok: false }

function validJournal(value: unknown, user: string): value is OperationJournal {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const journal = value as Record<string, unknown>
  return journal.userScope === user && journal.method === 'POST'
    && ['CREATE_PROPOSAL', 'CONFIRM', 'CANCEL', 'REPLACE_BINDING', 'PRESENTATION_CHALLENGE', 'PRESENTATION_REPORT'].includes(String(journal.kind))
    && ['PREPARED', 'RESPONSE_UNKNOWN', 'RESOURCE_RECEIVED', 'BUSINESS_REJECTED'].includes(String(journal.phase))
    && ['runtimeRef', 'runtimeGeneration', 'path', 'idempotencyKey'].every(key => typeof journal[key] === 'string' && !!journal[key].trim())
    && (journal.resourceId === null || typeof journal.resourceId === 'string')
    && !!journal.body && typeof journal.body === 'object' && !Array.isArray(journal.body)
    && typeof journal.updatedAt === 'string' && Number.isFinite(Date.parse(journal.updatedAt))
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
    loadingRequestSequence: 0,
    loadingRequestIds: [] as number[],
    error: '',
    errorCode: '',
    recoveryPending: false,
    recoveryAvailable: true,
    responseUnknown: false,
    contextRequestVersion: 0,
    executionPollInFlight: false,
    recoveryRequestVersion: 0,
    journalRevision: 0,
  }),
  getters: {
    context(state): VoiceRuntimeContext | null {
      return state.contexts.find(item => item.runtimeRef === state.selectedRuntimeRef) ?? null
    },
  },
  actions: {
    userScope() { return useAuthStore().user?.username ?? '' },
    startLoading() {
      const requestId = ++this.loadingRequestSequence
      this.loadingRequestIds.push(requestId)
      this.loading = true
      return requestId
    },
    finishLoading(requestId: number) {
      this.loadingRequestIds = this.loadingRequestIds.filter(id => id !== requestId)
      this.loading = this.loadingRequestIds.length > 0
    },
    invalidateRequests() {
      this.recoveryRequestVersion++
      this.loadingRequestIds = []
      this.loading = false
    },
    captureRequestScope(journal?: OperationJournal): () => boolean {
      const user = this.userScope()
      const run = this.expectedAlgorithmRunId
      const runtime = this.context?.runtimeRef
      const generation = this.context?.runtimeGeneration
      const version = this.recoveryRequestVersion
      return () => user === this.userScope() && run === this.expectedAlgorithmRunId
        && runtime === this.context?.runtimeRef && generation === this.context?.runtimeGeneration
        && version === this.recoveryRequestVersion
        && (!journal || this.loadJournal(presentationJournalKinds.has(journal.kind))?.idempotencyKey === journal.idempotencyKey)
    },
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
        this.journalRevision++
        this.recoveryAvailable = true
        return true
      } catch {
        this.recoveryAvailable = false
        this.errorCode = 'LOCAL_RECOVERY_UNAVAILABLE'
        this.error = '浏览器本地存储不可用，无法保证刷新后的安全恢复，本次控制操作已阻止。'
        return false
      }
    },
    readJournal(presentation = false): JournalRead {
      let raw: string | null
      try {
        raw = localStorage.getItem(this.journalKey(presentation))
      } catch {
        this.recoveryAvailable = false
        this.errorCode = 'LOCAL_RECOVERY_UNAVAILABLE'
        this.error = '无法读取本地恢复记录，已暂停下发；请恢复浏览器存储访问后重新核对。'
        return { ok: false }
      }
      if (raw === null) return { ok: true, journal: null }
      try {
        const journal: unknown = JSON.parse(raw)
        if (validJournal(journal, this.userScope())) return { ok: true, journal }
      } catch { /* Invalid JSON is not an absent journal. Preserve it for inspection. */ }
      this.recoveryAvailable = false
      this.errorCode = 'LOCAL_RECOVERY_CORRUPT'
      this.error = '本地恢复记录损坏或身份不一致，已保留原记录并阻止下发；请先核对上次请求，不要清空存储后重复执行。'
      return { ok: false }
    },
    loadJournal(presentation = false): OperationJournal | null {
      const read = this.readJournal(presentation)
      return read.ok ? read.journal : null
    },
    updateJournal(patch: Partial<OperationJournal>, presentation = false) {
      const journal = this.loadJournal(presentation)
      if (journal) this.saveJournal({ ...journal, ...patch, updatedAt: new Date().toISOString() })
    },
    persist() {
      const value: RecoveryState = { proposalId: this.proposal?.proposalId, executionId: this.execution?.executionId }
      try { localStorage.setItem(this.activeKey(), JSON.stringify(value)) } catch { this.recoveryAvailable = false }
    },
    clearActiveBlockedReason(): string {
      // Track local journal writes for consumers that cache this reason.
      void this.journalRevision
      if (this.loading || this.recoveryPending) return '请求正在处理或恢复，请等待完成后再清除视图。'
      if (this.responseUnknown) return '上次请求结果尚未确认，请先核对结果；清除视图不能取消设备动作。'
      if (this.execution && executionNeedsPolling(this.execution)) return '指令执行或展示结果仍待核对，请保留回执继续查询。'
      if (this.proposal?.status === 'AWAITING_CONFIRMATION') return '请先取消待确认提案或查询其最新状态，再清除视图。'
      if (this.proposal?.status === 'CONFIRMED' && !this.execution) return '提案已确认，但执行回执尚未恢复，请先核对结果。'
      try {
        for (const presentation of [false, true]) {
          const raw = localStorage.getItem(this.journalKey(presentation))
          if (!raw) continue
          const journal = JSON.parse(raw) as Partial<OperationJournal> | null
          if (!journal || journal.userScope !== this.userScope()
            || !['PREPARED', 'RESPONSE_UNKNOWN', 'RESOURCE_RECEIVED', 'BUSINESS_REJECTED'].includes(journal.phase ?? '')) {
            return '本地恢复记录无法核实，已保留记录，请先检查请求状态。'
          }
          if (journal.phase === 'PREPARED' || journal.phase === 'RESPONSE_UNKNOWN') {
            return '存在待核对的控制或展示请求，已保留原请求编号，请先恢复结果。'
          }
        }
      } catch { return '无法读取本地恢复记录，暂不能安全清除视图。' }
      return ''
    },
    clearActive() {
      if (this.clearActiveBlockedReason()) return false
      // Clearing the view is not cancellation. Keep both recovery journals;
      // only detach a settled receipt after storage confirms the removal.
      try { localStorage.removeItem(this.activeKey()) }
      catch {
        this.recoveryAvailable = false
        this.errorCode = 'LOCAL_RECOVERY_UNAVAILABLE'
        this.error = '无法清除本地回执，当前视图和恢复日志已保留。'
        return false
      }
      this.invalidateRequests()
      this.proposal = null
      this.execution = null
      this.error = ''
      this.errorCode = ''
      this.recoveryPending = false
      this.responseUnknown = false
      return true
    },
    clearStalePresentationRecovery() {
      if (this.execution?.state !== 'SUCCEEDED' || this.execution.presentationStatus !== 'STALE') return false
      this.presentationChallenge = null
      try {
        localStorage.removeItem(this.journalKey(true))
        this.journalRevision++
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
      // No registered runtime yet: this is preparation, not a failed connection.
      // Do not query or pick an unrelated runtime from the global context list.
      if (!expectedAlgorithmRunId) return false
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
        if (requestedBy === this.userScope() && requestVersion === this.contextRequestVersion
          && expectedAlgorithmRunId === this.expectedAlgorithmRunId) this.captureError(error, '无法读取语音控制上下文')
        return false
      }
    },
    async selectAlgorithmRun(algorithmRunId: string) {
      if (this.expectedAlgorithmRunId !== algorithmRunId) {
        const previousRunId = this.expectedAlgorithmRunId
        this.invalidateRequests()
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
        // An absent identity cannot prove that a saved receipt is obsolete.
        if (previousRunId && algorithmRunId) {
          try { localStorage.removeItem(this.activeKey()) } catch { this.recoveryAvailable = false }
        }
      }
      return this.refreshContexts(algorithmRunId)
    },
    async recover() {
      if (!this.userScope() || !this.expectedAlgorithmRunId) return
      const requestedBy = this.userScope()
      const requestedRun = this.expectedAlgorithmRunId
      this.invalidateRequests()
      const recoveryVersion = this.recoveryRequestVersion
      const currentRequest = () => recoveryVersion === this.recoveryRequestVersion
        && requestedBy === this.userScope() && requestedRun === this.expectedAlgorithmRunId
      this.recoveryPending = true
      try {
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
        const refreshed = await this.refreshContexts()
        if (!currentRequest()) return
        const controlRead = this.readJournal()
        const presentationRead = this.readJournal(true)
        if (!controlRead.ok || !presentationRead.ok) {
          this.responseUnknown = true
          return
        }
        if (!refreshed) {
          // Failure to establish the current context is NOT proof that the saved
          // request belongs to an obsolete run. Keep both journals and their keys.
          const pendingJournal = controlRead.journal
          this.responseUnknown = !!pendingJournal && ['PREPARED', 'RESPONSE_UNKNOWN'].includes(pendingJournal.phase)
          return
        }
        // Reads are available again. Every new write still has to persist its
        // journal successfully before any network command can be dispatched.
        this.recoveryAvailable = true
        const runtimeRef = this.context?.runtimeRef
        const runtimeGeneration = this.context?.runtimeGeneration
        const stillCurrent = () => currentRequest() && runtimeRef === this.context?.runtimeRef
          && runtimeGeneration === this.context?.runtimeGeneration
        let journal = controlRead.journal
        if (journal && (!this.context
          || journal.runtimeRef !== this.context.runtimeRef
          || journal.runtimeGeneration !== this.context.runtimeGeneration)) {
          // Do not replay an unknown-result write against a different runtime.
          try { localStorage.removeItem(this.journalKey()) } catch { this.recoveryAvailable = false }
          journal = null
        }
        if (journal && presentationJournalKinds.has(journal.kind)) {
          if (!this.saveJournal(journal)) return
          try { localStorage.removeItem(this.journalKey()) }
          catch { this.recoveryAvailable = false; return }
          journal = null
        }
        if (journal && ['PREPARED', 'RESPONSE_UNKNOWN'].includes(journal.phase)) {
          this.recoveryPending = true
          try {
            await this.replayJournal(journal, stillCurrent)
            if (!stillCurrent()) return
            this.responseUnknown = false
          } catch (error) {
            if (!stillCurrent()) return
            this.captureError(error, '上次请求结果仍待确认')
            this.responseUnknown = isUnknownResult(error)
            if (!this.responseUnknown) this.updateJournal({ phase: 'BUSINESS_REJECTED' })
          }
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
            await this.replayJournal(presentationJournal, stillCurrent)
          } catch {
            // Presentation recovery remains isolated from operator commands.
            // Keep its original key for the next recovery attempt.
          }
        }
        if (!stillCurrent()) return
        try {
          const raw = localStorage.getItem(this.activeKey())
          if (!raw) return
          const saved = JSON.parse(raw) as RecoveryState
          if (saved.proposalId) {
            const recoveredProposal = await fetchVoiceProposal(saved.proposalId)
            if (!stillCurrent()) return
            if (this.context
              && recoveredProposal.plan.runtimeRef === this.context.runtimeRef
              && recoveredProposal.plan.runtimeGeneration === this.context.runtimeGeneration) {
              this.proposal = recoveredProposal
            }
          }
          if (saved.executionId) {
            const recoveredExecution = await fetchVoiceExecution(saved.executionId)
            if (!stillCurrent()) return
            if (this.context
              && recoveredExecution.runtimeRef === this.context.runtimeRef
              && recoveredExecution.runtimeGeneration === this.context.runtimeGeneration) {
              this.execution = recoveredExecution
            }
          }
          if ((!this.proposal && !this.execution) && (saved.proposalId || saved.executionId)) {
            localStorage.removeItem(this.activeKey())
          }
        } catch (error) { if (stillCurrent()) this.captureError(error, '恢复上次指令状态失败') }
      } finally {
        if (recoveryVersion === this.recoveryRequestVersion) this.recoveryPending = false
      }
    },
    async replayJournal(journal: OperationJournal, requestCurrent: () => boolean = () => true) {
      const stillCurrent = () => requestCurrent() && journal.userScope === this.userScope()
        && journal.runtimeRef === this.context?.runtimeRef && journal.runtimeGeneration === this.context?.runtimeGeneration
      if (!stillCurrent()) return
      const presentation = presentationJournalKinds.has(journal.kind)
      if (journal.kind === 'CREATE_PROPOSAL') {
        const restored = await createVoiceProposal(journal.body as VoiceProposalRequest, journal.idempotencyKey, journal.interpretationId)
        if (!stillCurrent()) return
        this.proposal = restored
        this.execution = null
        this.updateJournal({ resourceId: this.proposal.proposalId, phase: 'RESOURCE_RECEIVED' }, presentation)
      } else if (journal.kind === 'CONFIRM') {
        const known = await fetchVoiceProposal(journal.resourceId ?? '')
        if (!stillCurrent()) return
        if (known.status === 'CONFIRMED' && known.executionId) {
          const restored = await fetchVoiceExecution(known.executionId)
          if (!stillCurrent()) return
          this.proposal = known
          this.execution = restored
        } else {
          const result = await confirmVoiceProposal(known.proposalId, journal.body as VoicePlanGuardRequest, journal.idempotencyKey)
          if (!stillCurrent()) return
          this.proposal = result.proposal
          this.execution = result.execution
        }
        this.updateJournal({ executionId: this.execution.executionId, phase: 'RESOURCE_RECEIVED' }, presentation)
      } else if (journal.kind === 'CANCEL') {
        const known = await fetchVoiceProposal(journal.resourceId ?? '')
        if (!stillCurrent()) return
        const restored = known.status === 'AWAITING_CONFIRMATION'
          ? await cancelVoiceProposal(known.proposalId, journal.body as VoicePlanGuardRequest, journal.idempotencyKey)
          : known
        if (!stillCurrent()) return
        this.proposal = restored
        this.updateJournal({ phase: 'RESOURCE_RECEIVED' })
      } else if (journal.kind === 'REPLACE_BINDING') {
        let restored = await fetchVoicePresentationBinding(journal.runtimeRef)
        if (!stillCurrent()) return
        if (restored.bindingId === (journal.body as VoicePresentationBindingRequest).expectedBindingId) {
          restored = await replaceVoicePresentationBinding(journal.runtimeRef, journal.body as VoicePresentationBindingRequest, journal.idempotencyKey)
          if (!stillCurrent()) return
        }
        this.presentationBinding = restored
        this.updateJournal({ resourceId: this.presentationBinding.bindingId, phase: 'RESOURCE_RECEIVED' }, presentation)
      } else if (journal.kind === 'PRESENTATION_CHALLENGE') {
        const restored = await createVoicePresentationChallenge(
          journal.runtimeRef,
          journal.body as VoicePresentationChallengeRequest,
          journal.idempotencyKey,
        )
        if (!stillCurrent()) return
        this.presentationChallenge = restored
        this.updateJournal({ resourceId: this.presentationChallenge.requestId, phase: 'RESOURCE_RECEIVED' }, presentation)
      } else if (journal.kind === 'PRESENTATION_REPORT') {
        const updated = await reportVoicePresentation(
          journal.runtimeRef,
          journal.body as VoicePresentationReportRequest,
          journal.idempotencyKey,
        )
        if (!stillCurrent()) return
        this.contexts = this.contexts.map(item => item.runtimeRef === updated.runtimeRef ? updated : item)
        this.updateJournal({ resourceId: (journal.body as VoicePresentationReportRequest).requestId, phase: 'RESOURCE_RECEIVED' }, presentation)
      }
      this.persist()
    },
    beginJournal(kind: JournalKind, path: string, body: JournalBody, resourceId: string | null = null, interpretationId?: string) {
      if (!this.context || !this.userScope()) return null
      const presentation = presentationJournalKinds.has(kind)
      const read = this.readJournal(presentation)
      if (!read.ok) return null
      let previous = read.journal
      if (previous
        && (previous.runtimeRef !== this.context.runtimeRef
          || previous.runtimeGeneration !== this.context.runtimeGeneration)) {
        try { localStorage.removeItem(this.journalKey(presentation)) }
        catch {
          this.recoveryAvailable = false
          this.errorCode = 'LOCAL_RECOVERY_UNAVAILABLE'
          this.error = '无法更新旧运行的恢复记录，已阻止新请求；请检查浏览器存储。'
          return null
        }
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
      const loadingRequest = this.startLoading()
      let stillCurrent = this.captureRequestScope()
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
        stillCurrent = this.captureRequestScope(journal)
        try {
          const result = await createVoiceProposal(body, journal.idempotencyKey, journal.interpretationId)
          if (!stillCurrent()) return
          this.proposal = result
        }
        catch (error) {
          if (!stillCurrent()) return
          this.responseUnknown = isUnknownResult(error)
          this.updateJournal({ phase: this.responseUnknown ? 'RESPONSE_UNKNOWN' : 'BUSINESS_REJECTED' })
          throw error
        }
        this.execution = null
        this.updateJournal({ resourceId: this.proposal.proposalId, phase: 'RESOURCE_RECEIVED' })
        this.responseUnknown = false
        this.persist()
      } catch (error) { if (stillCurrent()) this.captureError(error, '创建指令提案失败') }
      finally { this.finishLoading(loadingRequest) }
    },
    async confirm() {
      if (!this.proposal || !this.context) return
      const loadingRequest = this.startLoading()
      const body: VoicePlanGuardRequest = { expectedPlanVersion: this.proposal.planVersion, expectedPlanHash: this.proposal.planHash }
      const journal = this.beginJournal('CONFIRM', `/api/voice/commands/${this.proposal.proposalId}/confirm`, body, this.proposal.proposalId)
      if (!journal) { this.finishLoading(loadingRequest); return }
      const stillCurrent = this.captureRequestScope(journal)
      try {
        const result = await confirmVoiceProposal(this.proposal.proposalId, body, journal.idempotencyKey)
        if (!stillCurrent()) return
        this.proposal = result.proposal
        this.execution = result.execution
        this.error = ''
        this.updateJournal({ executionId: result.execution.executionId, phase: 'RESOURCE_RECEIVED' })
        this.persist()
      } catch (error) {
        if (!stillCurrent()) return
        this.responseUnknown = isUnknownResult(error)
        this.updateJournal({ phase: this.responseUnknown ? 'RESPONSE_UNKNOWN' : 'BUSINESS_REJECTED' })
        this.captureError(error, '确认指令失败')
      } finally { this.finishLoading(loadingRequest) }
    },
    async cancel() {
      if (!this.proposal || !this.context) return
      const loadingRequest = this.startLoading()
      const body: VoicePlanGuardRequest = { expectedPlanVersion: this.proposal.planVersion, expectedPlanHash: this.proposal.planHash }
      const journal = this.beginJournal('CANCEL', `/api/voice/commands/${this.proposal.proposalId}/cancel`, body, this.proposal.proposalId)
      if (!journal) { this.finishLoading(loadingRequest); return }
      const stillCurrent = this.captureRequestScope(journal)
      try {
        const result = await cancelVoiceProposal(this.proposal.proposalId, body, journal.idempotencyKey)
        if (!stillCurrent()) return
        this.proposal = result
        this.error = ''
        this.updateJournal({ phase: 'RESOURCE_RECEIVED' })
        this.persist()
      } catch (error) {
        if (!stillCurrent()) return
        this.responseUnknown = isUnknownResult(error)
        this.updateJournal({ phase: this.responseUnknown ? 'RESPONSE_UNKNOWN' : 'BUSINESS_REJECTED' })
        this.captureError(error, '取消指令失败')
      } finally { this.finishLoading(loadingRequest) }
    },
    async takePresentationBinding() {
      if (!this.context) return
      const loadingRequest = this.startLoading()
      let stillCurrent = this.captureRequestScope()
      let journal: OperationJournal | null = null
      try {
        const current = await fetchVoicePresentationBinding(this.context.runtimeRef)
        if (!stillCurrent()) return
        const body: VoicePresentationBindingRequest = { runtimeGeneration: this.context.runtimeGeneration, expectedBindingId: current.bindingId }
        journal = this.beginJournal('REPLACE_BINDING', `/api/voice/contexts/${this.context.runtimeRef}/presentation/bindings`, body)
        if (!journal) return
        stillCurrent = this.captureRequestScope(journal)
        const restored = await replaceVoicePresentationBinding(this.context.runtimeRef, body, journal.idempotencyKey)
        if (!stillCurrent()) return
        this.presentationBinding = restored
        this.updateJournal({ resourceId: this.presentationBinding.bindingId, phase: 'RESOURCE_RECEIVED' }, true)
        this.error = ''
        this.errorCode = ''
      } catch (error) {
        if (!stillCurrent()) return
        const unknown = isUnknownResult(error)
        if (journal) this.updateJournal({ phase: unknown ? 'RESPONSE_UNKNOWN' : 'BUSINESS_REJECTED' }, true)
        this.captureError(error, '建立 Unity 展示绑定失败')
      } finally { this.finishLoading(loadingRequest) }
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
      const stillCurrent = this.captureRequestScope(journal)
      try {
        const restored = await createVoicePresentationChallenge(this.context.runtimeRef, body, journal.idempotencyKey)
        if (!stillCurrent()) return null
        this.presentationChallenge = restored
        this.updateJournal({ resourceId: this.presentationChallenge.requestId, phase: 'RESOURCE_RECEIVED' }, true)
        this.error = ''
        this.errorCode = ''
        return this.presentationChallenge
      } catch (error) {
        if (!stillCurrent()) return null
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
      const stillCurrent = this.captureRequestScope(journal)
      let reportAccepted = false
      try {
        const updated = await reportVoicePresentation(this.context.runtimeRef, body, journal.idempotencyKey)
        if (!stillCurrent()) return false
        this.contexts = this.contexts.map(item => item.runtimeRef === updated.runtimeRef ? updated : item)
        this.presentationChallenge = null
        this.updateJournal({ phase: 'RESOURCE_RECEIVED' }, true)
        reportAccepted = true
        this.error = ''
        this.errorCode = ''
        if (body.kind === 'FRAME_APPLIED' && this.execution && this.execution.executionId === body.executionId) {
          const observedExecution = this.execution
          const restored = await fetchVoiceExecution(observedExecution.executionId)
          if (!stillCurrent()) return false
          if (this.execution !== observedExecution) return true
          if (restored.executionId !== observedExecution.executionId
            || restored.runtimeRef !== journal.runtimeRef || restored.runtimeGeneration !== journal.runtimeGeneration) return true
          this.execution = restored
          if (terminalStates.has(this.execution.state)) await this.refreshContexts()
          if (!stillCurrent()) return false
          this.persist()
        }
        return true
      } catch (error) {
        if (!stillCurrent()) return false
        const unknown = isUnknownResult(error)
        // A later receipt GET failing does not undo an acknowledged report POST.
        if (!reportAccepted) this.updateJournal({ phase: unknown ? 'RESPONSE_UNKNOWN' : 'BUSINESS_REJECTED' }, true)
        this.captureError(error, reportAccepted ? '刷新执行回执失败' : '提交 Unity 展示报告失败')
        return reportAccepted
      }
    },
    async poll() {
      if (this.executionPollInFlight) return
      this.executionPollInFlight = true
      const requestedBy = this.userScope()
      const requestedRun = this.expectedAlgorithmRunId
      const runtimeRef = this.context?.runtimeRef
      const runtimeGeneration = this.context?.runtimeGeneration
      const executionId = this.execution?.executionId
      const proposalId = this.proposal?.proposalId
      let observedExecution = this.execution
      let observedProposal = this.proposal
      const stillCurrent = () => requestedBy === this.userScope()
        && requestedRun === this.expectedAlgorithmRunId
        && runtimeRef === this.context?.runtimeRef && runtimeGeneration === this.context?.runtimeGeneration
        && executionId === this.execution?.executionId && proposalId === this.proposal?.proposalId
        && observedExecution === this.execution && observedProposal === this.proposal
      try {
        if (this.execution) {
          // Algorithm success and Unity presentation completion are separate.
          // Keep reading while the backend is still resolving PENDING to
          // REPORTED_APPLIED or STALE.
          if (!executionNeedsPolling(this.execution)) return
          const updated = await fetchVoiceExecution(this.execution.executionId)
          if (!stillCurrent() || updated.executionId !== executionId
            || updated.runtimeRef !== runtimeRef || updated.runtimeGeneration !== runtimeGeneration) return
          this.execution = updated
          observedExecution = this.execution
          if (terminalStates.has(this.execution.state)) await this.refreshContexts()
          if (!stillCurrent()) return
          this.persist()
        } else if (this.proposal?.status === 'AWAITING_CONFIRMATION') {
          const updated = await fetchVoiceProposal(this.proposal.proposalId)
          if (!stillCurrent() || updated.proposalId !== proposalId
            || updated.plan.runtimeRef !== runtimeRef || updated.plan.runtimeGeneration !== runtimeGeneration) return
          this.proposal = updated
          observedProposal = this.proposal
          this.persist()
        }
      } catch (error) { if (stillCurrent()) this.captureError(error, '刷新指令状态失败') }
      finally { this.executionPollInFlight = false }
    },
  },
})
