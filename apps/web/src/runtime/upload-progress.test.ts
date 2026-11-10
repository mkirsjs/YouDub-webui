import { expect, it, vi } from 'vitest'
import { attributeFiles, formFiles, snapshot, transferRate } from './upload-progress'
import { xhrUpload } from './xhr-upload'

it('attributes one multipart request across files without inventing a percent before the total is known', () => {
  const body = new FormData()
  body.set('request', '{}')
  body.set('input.video', new File(['12345'], 'clip.mp4'))
  body.set('input.subtitles', new File(['ab'], 'captions.srt'))
  expect(formFiles(body).map((file) => file.name)).toEqual(['clip.mp4', 'captions.srt'])
  expect(attributeFiles([{ name: 'clip.mp4', size: 5 }, { name: 'captions.srt', size: 2 }], 0, 0)).toEqual([
    { name: 'clip.mp4', size: 5, loaded: 0 }, { name: 'captions.srt', size: 2, loaded: 0 },
  ])
  expect(attributeFiles([{ name: 'clip.mp4', size: 5 }, { name: 'captions.srt', size: 2 }], 8, 10)[0]).toMatchObject({ loaded: 5 })
  expect(attributeFiles([{ name: 'clip.mp4', size: 5 }, { name: 'captions.srt', size: 2 }], 8, 10)[1]).toMatchObject({ loaded: 0 })
  expect(attributeFiles([{ name: 'clip.mp4', size: 5 }, { name: 'captions.srt', size: 2 }], 10, 10)[1]).toMatchObject({ loaded: 2 })
})

it('shows speed only after half a second of real byte growth', () => {
  expect(transferRate([{ at: 0, loaded: 0 }, { at: 100, loaded: 1000 }])).toBeUndefined()
  expect(transferRate([{ at: 0, loaded: 0 }, { at: 500, loaded: 1000 }])).toBe(2000)
  const view = snapshot([{ name: 'clip.mp4', size: 1000 }], 500, 1000, 'uploading', [{ at: 0, loaded: 0 }, { at: 500, loaded: 500 }])
  expect(view.bytesPerSecond).toBe(1000)
  expect(view.secondsRemaining).toBe(0.5)
  expect(snapshot([{ name: 'clip.mp4', size: 1000 }], 1000, 1000, 'importing', [{ at: 0, loaded: 0 }, { at: 500, loaded: 1000 }]).bytesPerSecond).toBeUndefined()
})

it('moves from partial bytes to server import and fails a dropped connection without a resume offset', async () => {
  const events: string[] = []
  class FakeXHR {
    upload: { onprogress?: (event: ProgressEvent) => void; onload?: () => void } = {}
    status = 200
    responseText = JSON.stringify({ id: 'task-1' })
    withCredentials = false
    open() {}
    send() {
      this.upload.onprogress?.({ lengthComputable: true, loaded: 40, total: 100 } as ProgressEvent)
      this.upload.onload?.()
      queueMicrotask(() => (this as { onload?: () => void }).onload?.())
    }
    getResponseHeader() { return 'application/json' }
  }
  vi.stubGlobal('XMLHttpRequest', FakeXHR)
  const body = new FormData(); body.set('input.video', new File(['x'], 'clip.mp4'))
  const result = await xhrUpload<{ id: string }>('/api/youdub/upload', body, { onProgress: (item) => events.push(item.phase) })
  expect(result.id).toBe('task-1')
  expect(events).toEqual(['uploading', 'importing'])

  class FailedXHR extends FakeXHR {
    send() { queueMicrotask(() => (this as { onerror?: () => void }).onerror?.()) }
  }
  vi.stubGlobal('XMLHttpRequest', FailedXHR)
  await expect(xhrUpload('/api/youdub/upload', body)).rejects.toMatchObject({ code: 'UPLOAD_FAILED' })
})
