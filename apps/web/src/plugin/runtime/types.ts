/** Browser services expose snapshots and explicit actions without a rendering dependency. */
export interface Observable<T> { subscribe(listener: () => void): () => void; getSnapshot(): T }
import type { UploadSnapshot } from '@/runtime/upload-progress'

export type Request = <T>(path: string, init?: RequestInit & { onUploadProgress?: (snapshot: UploadSnapshot) => void }) => Promise<T>
