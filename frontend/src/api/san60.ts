import { http } from './http'
import { fetchCsrfToken } from './auth'
import type { ApiResponse } from '@/types/api'
import type { San60Status } from '@/types/san60'

export async function fetchSan60State(signal?: AbortSignal): Promise<San60Status> {
  return (await http.get<ApiResponse<San60Status>>('/san60/state', { signal })).data.data
}
async function write(path: string, data: unknown) {
  const csrf = await fetchCsrfToken()
  return (await http.post<ApiResponse<{ ok: boolean; accepted?: number }>>(`/san60/${path}`, data,
    { headers: { [csrf.headerName]: csrf.token } })).data.data
}
export const controlSan60 = (command: Record<string, unknown>) => write('control', command)
export const ingestSan60 = (frames: unknown) => write('ingest', frames)
