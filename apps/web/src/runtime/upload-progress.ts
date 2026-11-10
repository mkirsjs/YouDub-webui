/** Byte progress for one whole-file multipart POST. There is no resume offset. */

export type UploadFile = { name: string; size: number; loaded: number }
export type UploadSnapshot = {
  phase: 'uploading' | 'importing'
  loaded: number
  total: number
  files: UploadFile[]
  bytesPerSecond?: number
  secondsRemaining?: number
}

export function formFiles(body: FormData): { name: string; size: number }[] {
  const files: { name: string; size: number }[] = []
  body.forEach((value, field) => {
    if (typeof value !== 'string') files.push({ name: value.name || field, size: value.size })
  })
  return files
}

/** Attribute one request's sent bytes to files in form order, after the multipart envelope. */
export function attributeFiles(files: { name: string; size: number }[], loaded: number, total: number): UploadFile[] {
  const payload = files.reduce((sum, file) => sum + file.size, 0)
  const overhead = total > 0 ? Math.max(0, total - payload) : 0
  let rest = total > 0 ? Math.max(0, loaded - overhead) : 0
  return files.map((file) => {
    const sent = Math.min(file.size, rest)
    rest -= sent
    return { name: file.name, size: file.size, loaded: sent }
  })
}

export function transferRate(samples: { at: number; loaded: number }[]): number | undefined {
  if (samples.length < 2) return undefined
  const first = samples[0]
  const last = samples[samples.length - 1]
  const seconds = (last.at - first.at) / 1000
  if (seconds < 0.5 || last.loaded <= first.loaded) return undefined
  return (last.loaded - first.loaded) / seconds
}

export function snapshot(files: { name: string; size: number }[], loaded: number, total: number, phase: UploadSnapshot['phase'], samples: { at: number; loaded: number }[]): UploadSnapshot {
  const bytesPerSecond = phase === 'uploading' ? transferRate(samples) : undefined
  const remaining = total > loaded && bytesPerSecond ? (total - loaded) / bytesPerSecond : undefined
  return {
    phase, loaded, total, files: attributeFiles(files, loaded, total),
    ...(bytesPerSecond ? { bytesPerSecond } : {}),
    ...(remaining !== undefined && Number.isFinite(remaining) ? { secondsRemaining: remaining } : {}),
  }
}

export function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value < 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let size = value
  let unit = 0
  while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit += 1 }
  return `${unit === 0 ? size.toFixed(0) : size.toFixed(1)} ${units[unit]}`
}
