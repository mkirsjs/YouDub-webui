/**
 * Exact public origin of one TLS-terminating reverse proxy.
 * The listener stays on loopback HTTP. A request is treated as that origin
 * only when the immediate peer is loopback and both forwarded headers equal
 * this origin exactly. Anything else keeps the raw Host and the listener
 * scheme, so localhost behavior and the Host/Origin fence stay intact.
 * @module @deepseek-ai/dsh-client-connection/public-origin
 */

import { assertTrustedAuthority } from './api-request-trust.ts'
import { isLoopbackHostname } from './loopback-hostname.ts'
import type { ConnectionTrustRequest } from './rpc.ts'

/** One configured browser origin. The authority grant is exact, including the default port. */
export interface PublicOrigin {
  /** `scheme://host` with the default port omitted, matching `URL.origin`. */
  readonly origin: string
  readonly protocol: 'http:' | 'https:'
  /** Host header spelling browsers use for this origin. */
  readonly hostHeader: string
  /** `hostname:port`, including 80 or 443, so the grant cannot match every port. */
  readonly trustedAuthority: string
}

/** Headers plus the optional socket peer. Forged forwarded headers from anywhere else are ignored. */
export interface TrustPeerRequest {
  readonly headers: ConnectionTrustRequest['headers']
  readonly socket?: { readonly remoteAddress?: string | null | undefined } | null
}

/** Facts the Host fence and cookie audience consume for one request. */
export interface RequestTrustView {
  readonly headers: ConnectionTrustRequest['headers']
  readonly secure: boolean
  readonly trustedHosts: readonly string[]
  readonly protocol: 'http:' | 'https:'
  /** True only when this request's forwarded identity matched {@link PublicOrigin}. */
  readonly proxied: boolean
}

const CONTROL = /[\u0000-\u0020\u007f]/u

/**
 * Parse one exact public origin. Wildcards, paths, userinfo, and lists are rejected.
 * @param value - `http(s)://host[:port][/]`.
 * @param label - flag or setting name included in the error.
 * @returns the normalized origin and the exact authority the fence will grant.
 */
export function parsePublicOrigin(value: string, label = 'publicOrigin'): PublicOrigin {
  if (CONTROL.test(value)) {
    throw new Error(`${label} must not contain ASCII whitespace or control characters`)
  }
  if (value.includes('*')) {
    throw new Error(`${label} must be one exact http(s) origin, not a wildcard`)
  }
  if (value.includes('?') || value.includes('#')) {
    throw new Error(`${label} must not include a query or fragment`)
  }
  if (!/^https?:\/\//iu.test(value)) {
    throw new Error(`${label} must be an absolute http or https origin`)
  }
  const url = URL.parse(value)
  if (url === null || (url.protocol !== 'http:' && url.protocol !== 'https:')) {
    throw new Error(`${label} must be an absolute http or https origin`)
  }
  if (url.username !== '' || url.password !== '') {
    throw new Error(`${label} must not include credentials`)
  }
  if (url.pathname !== '/' || url.search !== '' || url.hash !== '') {
    throw new Error(`${label} must be an origin with no path, query, or fragment`)
  }
  if (url.hostname === '' || url.hostname.includes('*')) {
    throw new Error(`${label} must name one exact host`)
  }
  const port = url.port !== '' ? url.port : (url.protocol === 'https:' ? '443' : '80')
  const trustedAuthority = `${url.hostname}:${port}`
  try {
    assertTrustedAuthority(trustedAuthority)
  } catch {
    throw new Error(`${label} must name one exact host`)
  }
  return {
    origin: url.origin,
    protocol: url.protocol as 'http:' | 'https:',
    hostHeader: url.host,
    trustedAuthority,
  }
}

/**
 * Choose the process's one public origin. An explicit flag wins; otherwise the
 * environment variable is used. An empty or malformed value fails the load.
 * @param flag - `--public-origin`, when the invocation passed it.
 * @param envValue - `YOUDUB_PUBLIC_ORIGIN`, when the flag was omitted.
 * @returns the parsed origin, or undefined when neither source is set.
 */
export function selectConfiguredPublicOrigin(
  flag: string | undefined,
  envValue: string | undefined,
): PublicOrigin | undefined {
  if (flag !== undefined) return parsePublicOrigin(flag, '--public-origin')
  if (envValue === undefined) return undefined
  return parsePublicOrigin(envValue, 'YOUDUB_PUBLIC_ORIGIN')
}

/**
 * Whether an advertised URL names the same origin the proxy is allowed to assert.
 * @param publicUrl - `--public-url` value, which may include a path prefix.
 * @param origin - configured public origin.
 * @returns true when both parse to the same `URL.origin`.
 */
export function publicUrlMatchesOrigin(publicUrl: string, origin: PublicOrigin): boolean {
  const url = URL.parse(publicUrl)
  return url !== null && url.origin === origin.origin
}

function headerValue(headers: ConnectionTrustRequest['headers'], name: string): string | undefined {
  if (headers instanceof Headers) {
    const value = headers.get(name)
    return value === null ? undefined : value
  }
  const value = headers[name]
  return typeof value === 'string' ? value : undefined
}

/** A single token. Comma-separated proxy lists and padded values fail closed. */
function exactToken(value: string | undefined): string | undefined {
  if (value === undefined || value === '' || /[\s,]/u.test(value)) return undefined
  return value
}

function forwardedOrigin(proto: string, host: string): string | undefined {
  const scheme = proto.toLowerCase()
  if (scheme !== 'http' && scheme !== 'https') return undefined
  if (/[@/\\?#*]/u.test(host)) return undefined
  const url = URL.parse(`${scheme}://${host}`)
  if (url === null || url.username !== '' || url.password !== '' || url.pathname !== '/') return undefined
  return url.origin
}

function isLoopbackPeer(address: string | null | undefined): boolean {
  if (address === undefined || address === null || address === '') return false
  if (address === '::1') return true
  const mapped = address.startsWith('::ffff:') ? address.slice('::ffff:'.length) : address
  return isLoopbackHostname(mapped)
}

function headersWithHost(
  headers: ConnectionTrustRequest['headers'],
  host: string,
): ConnectionTrustRequest['headers'] {
  if (headers instanceof Headers) {
    const copy = new Headers(headers)
    copy.set('host', host)
    return copy
  }
  return { ...headers, host }
}

/**
 * Resolve the authority a request is allowed to claim.
 * Forwarded headers count only from a loopback peer and only when their
 * origin is exactly the configured one. The raw Host is then replaced for
 * the fence and the cookie audience; the browser's Origin header is left
 * unchanged and still has to match.
 * @param request - node HTTP request, including its socket peer when present.
 * @param options - deployment grant and the listener's own scheme.
 * @returns the headers, scheme, and trusted authorities the fence should see.
 */
export function requestTrustView(
  request: TrustPeerRequest,
  options: {
    readonly publicOrigin?: PublicOrigin
    readonly trustedHosts: readonly string[]
    readonly listenerProtocol: 'http:' | 'https:'
  },
): RequestTrustView {
  const direct: RequestTrustView = {
    headers: request.headers,
    secure: options.listenerProtocol === 'https:',
    trustedHosts: options.trustedHosts,
    protocol: options.listenerProtocol,
    proxied: false,
  }
  const origin = options.publicOrigin
  if (origin === undefined || !isLoopbackPeer(request.socket?.remoteAddress)) return direct
  const proto = exactToken(headerValue(request.headers, 'x-forwarded-proto'))
  const host = exactToken(headerValue(request.headers, 'x-forwarded-host'))
  if (proto === undefined || host === undefined || forwardedOrigin(proto, host) !== origin.origin) return direct
  return {
    headers: headersWithHost(request.headers, origin.hostHeader),
    secure: origin.protocol === 'https:',
    trustedHosts: [...options.trustedHosts, origin.trustedAuthority],
    protocol: origin.protocol,
    proxied: true,
  }
}
