export interface RadarItem {
  id: string
  deviceId: string
  kind: 'OBSTACLE' | 'DETECTION' | 'POINTCLOUD' | 'RADAR_RETURN'
  range: number | null
  bearing: number | null
  x: number | null
  y: number | null
  z: number | null
  confidence: number | null
  timestampMs: number
}

export interface RadarOverview {
  connected: boolean
  onlineCount: number
  totalCount: number
  updatedAt: number
  obstacleCount: number
  detectionCount: number
  nearestObstacleRange: number | null
  latestTargetId: string
  items: RadarItem[]
  spectrumConnected: boolean
  spectrumVehicleId: string
  spectrumSensorId: string
  spectrumStreamId: string
  spectrumGatewaySequence: number | null
  spectrumSequence: number | null
  spectrumCapturedAt: number | null
  spectrumStartHz: number | null
  spectrumStopHz: number | null
  spectrumBinHz: number | null
  spectrumRbwHz: number | null
  spectrumRefLevelDbm: number | null
  spectrumPeakHz: number | null
  spectrumPeakDbm: number | null
  spectrumTemperatureC: number | null
  spectrumPowersDbm: number[]
}

export interface PointCloudFrame {
  schema_version: string
  message_type: 'pointcloud_frame'
  timestamp: number
  sequence: number
  source: string
  data: {
    stream_id: string
    vehicle_id: string
    frame_id: string
    timestamp: number
    point_count: number
    xyz: number[]
  }
}

export interface PointCloudLatest {
  streamId: string
  sequence: number | null
  receivedAtMs: number
  ageMs: number
  frame: PointCloudFrame
}
