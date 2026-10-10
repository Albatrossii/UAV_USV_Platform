export type VoiceIntent =
  | 'MISSION_START'
  | 'MISSION_PAUSE'
  | 'MISSION_RESUME'
  | 'MISSION_STOP'
  | 'SINGLE_DEVICE_CONTROL'
  | 'COMMAND_SEQUENCE'

export type VoiceAction = 'START' | 'PAUSE' | 'RESUME' | 'STOP'
export type VoicePlanAction = VoiceAction | 'DEVICE_COMMAND' | 'SEQUENCE'
export type DeviceCommandType = 'UAV_HOVER' | 'UAV_RESUME' | 'UAV_RETURN' | 'UAV_LAND' | 'USV_HOLD' | 'USV_RESUME' | 'USV_RETURN' | 'USV_STOP'
export type VoiceRuntimeState =
  | 'PREPARED' | 'PREVIEW' | 'RUNNING' | 'PAUSED' | 'STOPPED'
  | 'CANCELLED' | 'COMPLETED' | 'FAILED' | 'LOST'
export type VoiceProposalStatus =
  | 'AWAITING_CONFIRMATION' | 'CONFIRMED' | 'CANCELLED' | 'EXPIRED' | 'INVALIDATED'
export type VoiceExecutionState =
  | 'QUEUED' | 'DISPATCHED' | 'ACCEPTED' | 'EXECUTING'
  | 'SUCCEEDED' | 'REJECTED' | 'FAILED' | 'INVALIDATED' | 'TIMED_OUT'
export type VoiceExecutionOutcome = 'SUCCESS' | 'REJECTED' | 'FAILED' | 'UNKNOWN'
export type VoicePresentationStatus = 'NOT_REQUIRED' | 'PENDING' | 'REPORTED_APPLIED' | 'STALE'
export type VoicePresentationKind = 'SCENE_READY' | 'FRAME_APPLIED'

export interface VoiceRuntimeContext {
  runtimeRef: string
  runtimeGeneration: string
  contextVersion: number
  stateVersion: number
  runtimeScope: 'MISSION_CENTER'
  runtimeKind: 'STANDALONE_ALGORITHM'
  executionBackend: 'PYTHON_SIMULATION'
  algorithmRunId: string
  missionId: null
  missionRunId: null
  state: VoiceRuntimeState
  protocolVersion: 'algorithm.command.v1' | 'legacy'
  capabilities: string[]
  lastHeartbeatReceivedAt: string | null
  latestFrameSequence: number
  sceneReady: boolean
}

export interface VoiceProposalRequest {
  runtimeRef: string
  runtimeGeneration: string
  expectedContextVersion: number
  intent: VoiceIntent
}

export interface VoiceFrozenPlan {
  runtimeRef: string
  runtimeGeneration: string
  contextVersion: number
  stateVersion: number
  action: VoicePlanAction
  targetDeviceCode?: string
  deviceCommandType?: DeviceCommandType
  steps?: VoiceSequencePlanStep[]
  explicitDeviceCodes: string[]
  policyVersion: 'voice-p0.v1'
}

export interface VoiceSequencePlanStep {
  index: number
  action: 'START' | 'DEVICE_COMMAND'
  targetDeviceCode?: string
  deviceCommandType?: 'UAV_HOVER' | 'USV_HOLD'
}

export interface VoiceSequenceExecutionStep extends VoiceSequencePlanStep {
  state: 'PENDING' | VoiceExecutionState
  executionId: string | null
  errorCode: string | null
  startedAt?: string
  completedAt?: string
}

export interface VoiceProposal {
  proposalId: string
  interpretationId?: string
  status: VoiceProposalStatus
  planVersion: 1
  planHash: string
  plan: VoiceFrozenPlan
  requiresConfirmation: true
  createdAt: string
  expiresAt: string
  executionId: string | null
}

export interface VoicePlanGuardRequest {
  expectedPlanVersion: 1
  expectedPlanHash: string
}

export interface VoiceExecution {
  executionId: string
  proposalId: string
  commandId: string
  runtimeRef: string
  runtimeGeneration: string
  action: VoicePlanAction
  state: VoiceExecutionState
  outcome: VoiceExecutionOutcome
  errorCode: string | null
  timedOutAt: string | null
  presentationStatus: VoicePresentationStatus
  currentStepIndex?: number
  sequenceStatus?: 'EXECUTING_START' | 'WAITING_STEP_PRECONDITION' | 'EXECUTING_DEVICE_COMMAND' | 'SUCCEEDED' | 'FAILED'
  steps?: VoiceSequenceExecutionStep[]
  createdAt: string
  updatedAt: string
}

export interface VoiceConfirmResult {
  proposal: VoiceProposal
  execution: VoiceExecution
}

export interface VoiceMockRuntimeHint {
  algorithmRunId: string
  state: VoiceRuntimeState
  sceneReady: boolean
  deviceCodes: string[]
  latestFrameSequence: number
}

export interface VoicePresentationBinding {
  bindingId: string | null
  runtimeGeneration: string
}

export interface VoicePresentationBindingRequest {
  runtimeGeneration: string
  expectedBindingId: string | null
}

export interface VoicePresentationChallengeRequest {
  runtimeGeneration: string
  bindingId: string
  kind: VoicePresentationKind
  executionId: string | null
}

export interface VoicePresentationChallenge extends VoicePresentationChallengeRequest {
  requestId: string
  sequence: number
  expiresAt: string
}

export interface VoicePresentationReportRequest {
  runtimeGeneration: string
  bindingId: string
  kind: VoicePresentationKind
  executionId: string | null
  requestId: string
  sequence: number
  frameSequence: number
  applied: boolean
}

export interface UnityPresentationIdentity {
  protocolVersion: 'unity.presentation.v1'
  runtimeRef: string
  runtimeGeneration: string
  bindingId: string
  unityInstanceId: string
  sceneRevision: number
}

export interface UnityPresentationHello extends UnityPresentationIdentity {
  type: 'PRESENTATION_HELLO'
}

export interface UnityPresentationProbe extends UnityPresentationIdentity {
  type: 'PRESENTATION_PROBE'
  requestId: string
  sequence: number
  kind: VoicePresentationKind
  executionId: string | null
}

export interface UnityPresentationReady extends UnityPresentationIdentity {
  type: 'PRESENTATION_READY' | 'PRESENTATION_HEARTBEAT'
  scenarioReady: boolean
  lastAppliedFrameSequence: number
}

export interface UnityPresentationReport extends UnityPresentationIdentity {
  type: 'PRESENTATION_REPORT'
  requestId: string
  sequence: number
  kind: VoicePresentationKind
  executionId: string | null
  frameSequence: number
  applied: boolean
}

export type UnityPresentationOutgoing = UnityPresentationHello | UnityPresentationProbe
export type UnityPresentationIncoming = UnityPresentationReady | UnityPresentationReport

export type VoiceMockOutcome = 'SUCCESS' | 'REJECTED' | 'FAILED' | 'TIMEOUT_LATE_SUCCESS'
