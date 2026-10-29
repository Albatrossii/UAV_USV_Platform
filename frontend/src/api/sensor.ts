import { http } from './http'
import type { ApiResponse } from '@/types/api'
import type { PointCloudLatest, RadarOverview, San60SpatialOverview } from '@/types/sensor'

export async function fetchRadarOverview(): Promise<RadarOverview> {
  const response = await http.get<ApiResponse<RadarOverview>>('/sensors/radar')
  return response.data.data
}

export async function fetchSan60SpatialOverview(): Promise<San60SpatialOverview> {
  const response = await http.get<ApiResponse<San60SpatialOverview>>('/sensors/san60/spatial')
  return response.data.data
}

export async function fetchLatestPointCloudFrame(streamId = 'usv_01_mid360'): Promise<PointCloudLatest | null> {
  const response = await http.get<ApiResponse<PointCloudLatest | null>>('/sensors/pointcloud/latest', {
    params: { streamId },
  })
  return response.data.data
}
