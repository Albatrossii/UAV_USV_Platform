import {
  issueRuntimeCommand,
  type RuntimeCommandPayload,
  type RuntimeCommandResult,
  type VehicleCommandType,
} from '@/api/runtimeControl'

export type SingleDeviceCommand = Omit<RuntimeCommandPayload, 'commandType' | 'deviceCode'> & {
  commandType: VehicleCommandType
  deviceCode: string
}

/** Send one typed vehicle command to exactly one target through the existing command API. */
export function issueSingleDeviceCommand(command: SingleDeviceCommand): Promise<RuntimeCommandResult> {
  const deviceCode = command.deviceCode.trim().toUpperCase().replace(/_/g, '-')
  if (!deviceCode) throw new Error('单机控制需要指定设备编号')

  const requiredPrefix = command.commandType.startsWith('UAV_') ? 'UAV-' : 'USV-'
  if (!deviceCode.startsWith(requiredPrefix)) {
    throw new Error(`${command.commandType.startsWith('UAV_') ? '无人机' : '无人艇'}指令与目标设备类型不匹配`)
  }

  return issueRuntimeCommand({ ...command, deviceCode })
}
