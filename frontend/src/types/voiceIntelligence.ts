import type { DeviceCommandType, VoiceAction, VoiceIntent } from './voiceControl'

export type VoiceInputStage =
  | 'IDLE'
  | 'RECORDING'
  | 'TRANSCRIBING'
  | 'READY_TO_PARSE'
  | 'PARSING'
  | 'CANDIDATE'
  | 'NEEDS_CLARIFICATION'
  | 'UNSUPPORTED'
  | 'ERROR'

export type VoiceParseStatus = 'CANDIDATE' | 'NEEDS_CLARIFICATION' | 'UNSUPPORTED' | 'NOT_ACTIONABLE'
export type VoiceParseReason =
  | 'AMBIGUOUS_ACTION'
  | 'NEGATED_ACTION'
  | 'NO_SUPPORTED_ACTION'
  | 'AMBIGUOUS_TARGET'
  | 'UNSUPPORTED_CAPABILITY'
  | 'UNSUPPORTED_TARGETING'

export interface VoiceAudioInput {
  audio: Blob
  locale: string
  requestId: string
  signal?: AbortSignal
}

export interface VoiceTranscript {
  requestId: string
  text: string
  locale: string
  durationMs: number
  provider: string
  model: string
}

export interface VoiceParseRequest {
  requestId: string
  text: string
  locale: string
  allowedActions: VoiceAction[]
  availableDeviceCodes: string[]
  runtimeContext: VoiceInterpretationRuntimeContext | null
  signal?: AbortSignal
}

export interface VoiceInterpretationRuntimeContext {
  runtimeRef: string
  runtimeGeneration: string
  contextVersion: number
}

export interface VoiceIntentCandidate {
  status: 'CANDIDATE'
  requestId: string
  intent: VoiceIntent
  action: VoiceAction | 'DEVICE_COMMAND'
  targetDeviceCode?: string
  deviceCommandType?: DeviceCommandType
  normalizedText: string
  confidence: number | null
  provider: string
  model: string
}

export interface VoiceIntentNotReady {
  status: Exclude<VoiceParseStatus, 'CANDIDATE'>
  requestId: string
  reason: VoiceParseReason
  message: string
  normalizedText: string
  provider: string
  model: string
}

export type VoiceParseResult = VoiceIntentCandidate | VoiceIntentNotReady

/** Provider-neutral seam. Implementations belong in the backend integration layer. */
export interface VoiceIntelligenceAdapter {
  readonly name: string
  readonly mode: 'MOCK' | 'BACKEND'
  transcribe(input: VoiceAudioInput): Promise<VoiceTranscript>
  parse(input: VoiceParseRequest): Promise<VoiceParseResult>
}

export type VoiceIntelligenceErrorCode =
  | 'VOICE_INVALID_REQUEST'
  | 'VOICE_AUDIO_EMPTY'
  | 'VOICE_AUDIO_TOO_LARGE'
  | 'VOICE_AUDIO_FORMAT_UNSUPPORTED'
  | 'VOICE_TEXT_TOO_LONG'
  | 'VOICE_REQUEST_CANCELLED'
  | 'VOICE_TRANSCRIPTION_TIMEOUT'
  | 'VOICE_PARSE_TIMEOUT'
  | 'VOICE_PROVIDER_UNAVAILABLE'
  | 'VOICE_MALFORMED_RESPONSE'

export class VoiceIntelligenceError extends Error {
  constructor(message: string, public readonly code: VoiceIntelligenceErrorCode) {
    super(message)
    this.name = 'VoiceIntelligenceError'
  }
}
