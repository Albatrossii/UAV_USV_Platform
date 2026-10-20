export interface WorkspaceHeaderState {
  deviceCode: string
  deviceContext: string
  statuses: Array<{
    label: string
    tone: 'online' | 'offline' | 'info'
  }>
}
