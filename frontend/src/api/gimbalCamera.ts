import { fetchCsrfToken } from './auth'
import { http } from './http'
import type { ApiResponse } from '@/types/api'
import type { GimbalCameraCommand, GimbalCameraCommandResult, GimbalCameraStatus } from '@/types/gimbalCamera'
export async function fetchGimbalCameraStatus(): Promise<GimbalCameraStatus> {
  const response = await http.get<ApiResponse<GimbalCameraStatus>>('/gimbal-camera/status')
  return response.data.data
}
export async function fetchGimbalCameraFrame(since: number): Promise<{ blob: Blob; timestamp: number } | null> {
  const response = await http.get<Blob>('/gimbal-camera/frame', {
    params: { since },
    responseType: 'blob',
    timeout: 3500,
    headers: { Accept: 'image/jpeg' },
    validateStatus: (status) => status === 200 || status === 204,
  })
  if (response.status !== 200 || response.data.size === 0) return null
  return { blob: response.data, timestamp: Number(response.headers['x-frame-timestamp']) || Date.now() }
}
export async function sendGimbalCameraCommand(command: GimbalCameraCommand): Promise<GimbalCameraCommandResult> {
  const csrf = await fetchCsrfToken()
  const response = await http.post<ApiResponse<GimbalCameraCommandResult>>('/gimbal-camera/commands', command, { headers: { [csrf.headerName]: csrf.token } })
  return response.data.data
}
