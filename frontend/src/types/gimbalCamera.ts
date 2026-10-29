export interface GimbalCameraStatus {
  configured: boolean
  connected: boolean
  deviceName: string
  controlProtocol: string
  streamProtocol: string
  playbackUrl: string | null
  yawDegrees: number | null
  pitchDegrees: number | null
  rollDegrees: number | null
  opticalZoom: number | null
  infraredZoom: number | null
  imageMode: string | null
  detail: string
  controlEnabled: boolean
  motionActive: boolean
  watchdogTimeoutSec: number
  safetyFault: string | null
  supportedCommands: string[]
  zoomControlEnabled: boolean
}
export interface GimbalCameraCommand { command: string; parameters?: Record<string, string | number | boolean> }
export interface GimbalCameraCommandResult { commandId: string; command: string; status: string; detail: string }
