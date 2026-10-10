import type { ApiResponse } from '@/types/api'
import type {
  VoiceConfirmResult,
  VoiceExecution,
  VoiceMockOutcome,
  VoiceMockRuntimeHint,
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
import { fetchCsrfToken } from './auth'
import { ApiClientError, http } from './http'

const mockEnabled = import.meta.env.VITE_VOICE_P0_MOCK === 'true'
const proposalReplay = new Map<string, VoiceProposal>()
const confirmReplay = new Map<string, VoiceConfirmResult>()
const proposals = new Map<string, VoiceProposal>()
const executions = new Map<string, VoiceExecution & { mockOutcome?: VoiceMockOutcome; reads?: number }>()
const mockRef = '11111111-1111-4111-8111-111111111111'
const mockGeneration = '22222222-2222-4222-8222-222222222222'
let mockOutcome: VoiceMockOutcome = 'SUCCESS'
let mockBindingId: string | null = null
let mockDevices = ['UAV-001', 'USV-001']
let mockContext: VoiceRuntimeContext = {
  runtimeRef: mockRef,
  runtimeGeneration: mockGeneration,
  contextVersion: 1,
  stateVersion: 1,
  runtimeScope: 'MISSION_CENTER',
  runtimeKind: 'STANDALONE_ALGORITHM',
  executionBackend: 'PYTHON_SIMULATION',
  algorithmRunId: '7001',
  missionId: null,
  missionRunId: null,
  state: 'PREPARED',
  protocolVersion: 'algorithm.command.v1',
  capabilities: ['START', 'PAUSE', 'RESUME', 'STOP'],
  lastHeartbeatReceivedAt: new Date().toISOString(),
  latestFrameSequence: 0,
  sceneReady: false,
}

function uuid() {
  return crypto.randomUUID().toLowerCase()
}

function clone<T>(value: T): T {
  return structuredClone(value)
}

function publicExecution(value: VoiceExecution & { mockOutcome?: VoiceMockOutcome; reads?: number }): VoiceExecution {
  const { mockOutcome: _mockOutcome, reads: _reads, ...execution } = value
  return clone(execution)
}

function assertMockPlan(proposal: VoiceProposal, guard: VoicePlanGuardRequest) {
  if (guard.expectedPlanVersion !== 1 || !/^[0-9a-f]{64}$/.test(guard.expectedPlanHash)) {
    throw new ApiClientError('请求格式或版本不受支持，请刷新页面后重试', 400, 'INVALID_REQUEST')
  }
  if (proposal.planVersion !== guard.expectedPlanVersion || proposal.planHash !== guard.expectedPlanHash) {
    throw new ApiClientError('提案信息不一致，请重新获取提案', 409, 'PLAN_MISMATCH')
  }
}

async function planHash(plan: VoiceProposal['plan']) {
  const canonical = JSON.stringify(plan, Object.keys(plan).sort())
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(canonical))
  return [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, '0')).join('')
}

export const voiceP0MockEnabled = mockEnabled

export function configureVoiceP0MockRuntime(hint: VoiceMockRuntimeHint) {
  if (!mockEnabled) return
  const identityChanged = mockContext.algorithmRunId !== hint.algorithmRunId
  const nextDevices = [...new Set(hint.deviceCodes)].sort()
  const planChanged = identityChanged
    || mockContext.state !== hint.state
    || mockContext.sceneReady !== hint.sceneReady
    || nextDevices.join('\0') !== mockDevices.join('\0')
  mockDevices = nextDevices
  mockContext = {
    ...mockContext,
    runtimeGeneration: identityChanged ? uuid() : mockContext.runtimeGeneration,
    algorithmRunId: hint.algorithmRunId,
    contextVersion: planChanged ? mockContext.contextVersion + 1 : mockContext.contextVersion,
    stateVersion: mockContext.state === hint.state ? mockContext.stateVersion : mockContext.stateVersion + 1,
    state: hint.state,
    sceneReady: hint.sceneReady,
    latestFrameSequence: hint.latestFrameSequence,
    lastHeartbeatReceivedAt: new Date().toISOString(),
  }
}

export function setVoiceP0MockOutcome(value: VoiceMockOutcome) {
  mockOutcome = value
}

async function csrfHeaders(idempotencyKey: string) {
  const token = await fetchCsrfToken()
  return { [token.headerName]: token.token, 'Idempotency-Key': idempotencyKey }
}

export async function fetchVoiceContexts(): Promise<VoiceRuntimeContext[]> {
  if (mockEnabled) {
    if (!['STOPPED', 'CANCELLED', 'COMPLETED', 'FAILED', 'LOST'].includes(mockContext.state)) {
      mockContext.lastHeartbeatReceivedAt = new Date().toISOString()
    }
    return [clone(mockContext)]
  }
  const response = await http.get<ApiResponse<VoiceRuntimeContext[]>>('/voice/contexts')
  return response.data.data
}

export async function fetchVoiceContext(runtimeRef: string): Promise<VoiceRuntimeContext> {
  if (mockEnabled) {
    if (runtimeRef !== mockContext.runtimeRef) throw new ApiClientError('运行实例不存在', 404, 'RUNTIME_NOT_FOUND')
    return clone(mockContext)
  }
  const response = await http.get<ApiResponse<VoiceRuntimeContext>>(`/voice/contexts/${runtimeRef}`)
  return response.data.data
}

export async function createVoiceProposal(payload: VoiceProposalRequest, idempotencyKey: string, interpretationId?: string): Promise<VoiceProposal> {
  if (mockEnabled) {
    const replay = proposalReplay.get(idempotencyKey)
    if (replay) return clone(replay)
    if (payload.runtimeRef !== mockContext.runtimeRef || payload.runtimeGeneration !== mockContext.runtimeGeneration) {
      throw new ApiClientError('运行代际已变化，请刷新上下文', 409, 'GENERATION_MISMATCH')
    }
    if (payload.expectedContextVersion !== mockContext.contextVersion) {
      throw new ApiClientError('运行上下文已变化，请刷新后重试', 409, 'CONTEXT_CHANGED')
    }
    const action = (payload.intent === 'COMMAND_SEQUENCE'
      ? 'SEQUENCE'
      : payload.intent.replace('MISSION_', '')) as VoiceProposal['plan']['action']
    const plan: VoiceProposal['plan'] = {
      runtimeRef: mockContext.runtimeRef,
      runtimeGeneration: mockContext.runtimeGeneration,
      contextVersion: mockContext.contextVersion,
      stateVersion: mockContext.stateVersion,
      action,
      explicitDeviceCodes: mockDevices.length ? mockDevices : ['UNASSIGNED-001'],
      policyVersion: 'voice-p0.v1',
    }
    const now = Date.now()
    const proposal: VoiceProposal = {
      proposalId: uuid(), status: 'AWAITING_CONFIRMATION', planVersion: 1,
      planHash: await planHash(plan), plan, requiresConfirmation: true,
      createdAt: new Date(now).toISOString(), expiresAt: new Date(now + 30_000).toISOString(), executionId: null,
      ...(interpretationId ? { interpretationId } : {}),
    }
    proposals.set(proposal.proposalId, proposal)
    proposalReplay.set(idempotencyKey, proposal)
    return clone(proposal)
  }
  const response = await http.post<ApiResponse<VoiceProposal>>('/voice/commands/proposals', payload, {
    headers: {
      ...await csrfHeaders(idempotencyKey),
      ...(interpretationId ? { 'X-Voice-Interpretation-ID': interpretationId } : {}),
    },
  })
  return response.data.data
}

export async function fetchVoiceProposal(proposalId: string): Promise<VoiceProposal> {
  if (mockEnabled) {
    const proposal = proposals.get(proposalId)
    if (!proposal) throw new ApiClientError('提案不存在', 404, 'PROPOSAL_NOT_FOUND')
    if (proposal.status === 'AWAITING_CONFIRMATION' && Date.parse(proposal.expiresAt) <= Date.now()) proposal.status = 'EXPIRED'
    return clone(proposal)
  }
  const response = await http.get<ApiResponse<VoiceProposal>>(`/voice/commands/${proposalId}`)
  return response.data.data
}

export async function confirmVoiceProposal(proposalId: string, payload: VoicePlanGuardRequest, idempotencyKey: string): Promise<VoiceConfirmResult> {
  if (mockEnabled) {
    const proposal = await fetchVoiceProposal(proposalId)
    assertMockPlan(proposal, payload)
    const replay = confirmReplay.get(idempotencyKey)
    if (replay) return clone(replay)
    if (proposal.status !== 'AWAITING_CONFIRMATION') throw new ApiClientError('提案已不可确认', 409, `PROPOSAL_${proposal.status}`)
    const now = new Date().toISOString()
    const execution: VoiceExecution & { mockOutcome?: VoiceMockOutcome; reads?: number } = {
      executionId: uuid(), proposalId, commandId: uuid(), runtimeRef: proposal.plan.runtimeRef,
      runtimeGeneration: proposal.plan.runtimeGeneration, action: proposal.plan.action,
      state: 'QUEUED', outcome: 'UNKNOWN', errorCode: null, timedOutAt: null,
      presentationStatus: 'NOT_REQUIRED', createdAt: now, updatedAt: now,
      mockOutcome, reads: 0,
    }
    proposal.status = 'CONFIRMED'
    proposal.executionId = execution.executionId
    proposals.set(proposalId, proposal)
    executions.set(execution.executionId, execution)
    const result = { proposal: clone(proposal), execution: publicExecution(execution) }
    confirmReplay.set(idempotencyKey, result)
    return result
  }
  const response = await http.post<ApiResponse<VoiceConfirmResult>>(`/voice/commands/${proposalId}/confirm`, payload, {
    headers: await csrfHeaders(idempotencyKey),
  })
  return response.data.data
}

export async function cancelVoiceProposal(proposalId: string, payload: VoicePlanGuardRequest, idempotencyKey: string): Promise<VoiceProposal> {
  if (mockEnabled) {
    const proposal = await fetchVoiceProposal(proposalId)
    assertMockPlan(proposal, payload)
    if (proposal.status !== 'AWAITING_CONFIRMATION') throw new ApiClientError('提案已不可取消', 409, `PROPOSAL_${proposal.status}`)
    proposal.status = 'CANCELLED'
    proposals.set(proposalId, proposal)
    return clone(proposal)
  }
  const response = await http.post<ApiResponse<VoiceProposal>>(`/voice/commands/${proposalId}/cancel`, payload, {
    headers: await csrfHeaders(idempotencyKey),
  })
  return response.data.data
}

export async function fetchVoiceExecution(executionId: string): Promise<VoiceExecution> {
  if (mockEnabled) {
    const execution = executions.get(executionId)
    if (!execution) throw new ApiClientError('执行记录不存在', 404, 'EXECUTION_NOT_FOUND')
    execution.reads = (execution.reads ?? 0) + 1
    const reads = execution.reads
    if (reads === 1) execution.state = 'DISPATCHED'
    else if (reads === 2) execution.state = 'EXECUTING'
    else if (reads >= 3) {
      if (execution.mockOutcome === 'REJECTED') Object.assign(execution, { state: 'REJECTED', outcome: 'REJECTED', errorCode: 'INVALID_STATE' })
      else if (execution.mockOutcome === 'FAILED') Object.assign(execution, { state: 'FAILED', outcome: 'FAILED', errorCode: 'ALGORITHM_ERROR' })
      else if (execution.mockOutcome === 'TIMEOUT_LATE_SUCCESS' && reads < 6) Object.assign(execution, { state: 'TIMED_OUT', outcome: 'UNKNOWN', errorCode: 'ACK_TIMEOUT', timedOutAt: new Date().toISOString() })
      else {
        Object.assign(execution, {
          state: 'SUCCEEDED', outcome: 'SUCCESS', errorCode: null,
          presentationStatus: ['START', 'RESUME'].includes(execution.action) ? 'PENDING' : 'NOT_REQUIRED',
        })
        const nextState = execution.action === 'PAUSE' ? 'PAUSED'
          : execution.action === 'STOP' ? 'STOPPED' : 'RUNNING'
        if (mockContext.state !== nextState) {
          mockContext.state = nextState
          mockContext.stateVersion += 1
          mockContext.contextVersion += 1
        }
      }
    }
    execution.updatedAt = new Date().toISOString()
    executions.set(executionId, execution)
    return publicExecution(execution)
  }
  const response = await http.get<ApiResponse<VoiceExecution>>(`/voice/executions/${executionId}`)
  return response.data.data
}

export async function fetchVoicePresentationBinding(runtimeRef: string): Promise<VoicePresentationBinding> {
  if (mockEnabled) return { bindingId: mockBindingId, runtimeGeneration: mockContext.runtimeGeneration }
  const response = await http.get<ApiResponse<VoicePresentationBinding>>(`/voice/contexts/${runtimeRef}/presentation/binding`)
  return response.data.data
}

export async function replaceVoicePresentationBinding(runtimeRef: string, payload: VoicePresentationBindingRequest, idempotencyKey: string): Promise<VoicePresentationBinding> {
  if (mockEnabled) {
    if (payload.runtimeGeneration !== mockContext.runtimeGeneration || payload.expectedBindingId !== mockBindingId) {
      throw new ApiClientError('展示绑定或运行代际已变化', 409, 'CONTEXT_CHANGED')
    }
    mockBindingId = uuid()
    mockContext.sceneReady = false
    mockContext.contextVersion += 1
    return { bindingId: mockBindingId, runtimeGeneration: mockContext.runtimeGeneration }
  }
  const response = await http.post<ApiResponse<VoicePresentationBinding>>(`/voice/contexts/${runtimeRef}/presentation/bindings`, payload, {
    headers: await csrfHeaders(idempotencyKey),
  })
  return response.data.data
}

export async function createVoicePresentationChallenge(runtimeRef: string, payload: VoicePresentationChallengeRequest, idempotencyKey: string): Promise<VoicePresentationChallenge> {
  if (mockEnabled) {
    if (!mockBindingId || payload.bindingId !== mockBindingId) throw new ApiClientError('展示绑定已变化', 409, 'CONTEXT_CHANGED')
    return { ...payload, requestId: uuid(), sequence: mockContext.latestFrameSequence + 1, expiresAt: new Date(Date.now() + 5000).toISOString() }
  }
  const response = await http.post<ApiResponse<VoicePresentationChallenge>>(`/voice/contexts/${runtimeRef}/presentation/challenges`, payload, {
    headers: await csrfHeaders(idempotencyKey),
  })
  return response.data.data
}

export async function reportVoicePresentation(runtimeRef: string, payload: VoicePresentationReportRequest, idempotencyKey: string): Promise<VoiceRuntimeContext> {
  if (mockEnabled) {
    if (!mockBindingId || payload.bindingId !== mockBindingId) throw new ApiClientError('展示绑定已变化', 409, 'CONTEXT_CHANGED')
    mockContext.sceneReady = payload.applied
    mockContext.contextVersion += 1
    if (payload.kind === 'FRAME_APPLIED' && payload.executionId) {
      const execution = executions.get(payload.executionId)
      if (execution && payload.applied) execution.presentationStatus = 'REPORTED_APPLIED'
    }
    return clone(mockContext)
  }
  const response = await http.post<ApiResponse<VoiceRuntimeContext>>(`/voice/contexts/${runtimeRef}/presentation/reports`, payload, {
    headers: await csrfHeaders(idempotencyKey),
  })
  return response.data.data
}
