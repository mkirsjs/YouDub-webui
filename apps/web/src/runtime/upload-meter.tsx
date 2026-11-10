import type { UploadSnapshot } from './upload-progress'
import { formatBytes } from './upload-progress'

export function uploadErrorText(error: string, text: (key: string) => string): string {
  if (error === 'UPLOAD_INTERRUPTED') return text('upload.interrupted')
  if (error === 'UPLOAD_FAILED') return text('upload.failed')
  return error
}

/** Transfer progress only. One hundred percent means the bytes were sent, not that dubbing finished. */
export function UploadMeter({ snapshot, sending, importing, sent, noResume, speed }: {
  snapshot: UploadSnapshot
  sending: string
  importing: string
  sent: string
  noResume: string
  speed: string
}) {
  const percent = snapshot.total > 0 ? Math.min(100, Math.floor(snapshot.loaded * 100 / snapshot.total)) : null
  return <div className="space-y-2" role="status">
    <p className="text-sm">{snapshot.phase === 'importing' ? importing : sending}</p>
    <progress className="h-2 w-full" aria-label={sending} value={snapshot.total > 0 ? snapshot.loaded : undefined} max={snapshot.total > 0 ? snapshot.total : undefined} />
    <p className="text-xs text-muted-foreground">{formatBytes(snapshot.loaded)} / {snapshot.total > 0 ? formatBytes(snapshot.total) : '…'}{percent !== null ? ` (${percent}%)` : ''}</p>
    {snapshot.files.length > 0 && <ul className="space-y-1 text-xs text-muted-foreground">{snapshot.files.map((file) => <li key={file.name}>{file.name} {formatBytes(file.loaded)} / {formatBytes(file.size)}</li>)}</ul>}
    {snapshot.bytesPerSecond ? <p className="text-xs text-muted-foreground">{speed}</p> : null}
    {snapshot.phase === 'uploading' && snapshot.total > 0 && snapshot.loaded >= snapshot.total ? <p className="text-xs">{sent}</p> : null}
    <p className="text-xs text-muted-foreground">{noResume}</p>
  </div>
}
