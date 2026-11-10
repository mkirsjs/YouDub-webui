import { responseBody } from './http'
import { formFiles, snapshot, type UploadSnapshot } from './upload-progress'

export class UploadError extends Error {
  readonly code: 'UPLOAD_INTERRUPTED' | 'UPLOAD_FAILED'
  constructor(message: string, code: 'UPLOAD_INTERRUPTED' | 'UPLOAD_FAILED') {
    super(message)
    this.name = 'UploadError'
    this.code = code
  }
}

/** Send one multipart body and report bytes the browser has handed to the connection. */
export function xhrUpload<T>(url: string, body: FormData, init: { signal?: AbortSignal; onProgress?: (snapshot: UploadSnapshot) => void } = {}): Promise<T> {
  const files = formFiles(body)
  const samples: { at: number; loaded: number }[] = []
  let knownTotal = 0
  const emit = (phase: UploadSnapshot['phase'], loaded: number, total: number) => {
    if (total > 0) knownTotal = total
    samples.push({ at: performance.now(), loaded })
    while (samples.length > 2 && samples[samples.length - 1].at - samples[0].at > 5000) samples.shift()
    init.onProgress?.(snapshot(files, loaded, total, phase, samples))
  }
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', url)
    xhr.withCredentials = true
    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable) {
        init.onProgress?.({ phase: 'uploading', loaded: event.loaded, total: 0, files: files.map((file) => ({ ...file, loaded: 0 })) })
        return
      }
      emit('uploading', event.loaded, event.total)
    }
    xhr.upload.onload = () => emit('importing', knownTotal, knownTotal)
    xhr.onload = () => {
      const response = new Response(xhr.responseText, { status: xhr.status, headers: { 'content-type': xhr.getResponseHeader('content-type') ?? '' } })
      responseBody<T>(response).then(resolve, reject)
    }
    xhr.onerror = () => reject(new UploadError('UPLOAD_FAILED', 'UPLOAD_FAILED'))
    xhr.onabort = () => reject(new UploadError('UPLOAD_INTERRUPTED', 'UPLOAD_INTERRUPTED'))
    const abort = () => xhr.abort()
    init.signal?.addEventListener('abort', abort, { once: true })
    xhr.send(body)
  })
}

