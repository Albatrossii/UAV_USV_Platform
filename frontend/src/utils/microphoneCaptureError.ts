/** Capture failures happen before either speech recognition provider is called. */
export function microphoneCaptureError(error: unknown) {
  const name = error && typeof error === 'object' && 'name' in error ? String(error.name) : ''
  switch (name) {
    case 'NotFoundError':
      return '未检测到可用麦克风输入。请在 Windows 声音设置的“输入”中连接或启用麦克风；蓝牙耳机能播放声音不代表麦克风已连接。'
    case 'OverconstrainedError':
      return '所选麦克风不可用，请刷新设备列表并重新选择，或使用系统默认麦克风。'
    case 'NotAllowedError':
    case 'SecurityError':
      return '麦克风权限被拒绝，请检查浏览器网站权限和系统麦克风权限，或使用文字输入。'
    case 'NotReadableError':
      return '无法读取麦克风，请检查设备连接及是否被其他程序占用，然后重试。'
    default:
      return error instanceof Error ? error.message : '无法录音，请检查麦克风。'
  }
}
