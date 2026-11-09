/**
 * Exact public-origin proxy: API and WebSocket admission, cookies, and auth.
 * Requests carry the headers nginx sends to the loopback host.
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash, randomBytes } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import { createServer, request as httpRequest } from 'node:http'
import { registerHooks } from 'node:module'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const sourceRoot = join(here, '../../../vendor/deepseek-harness/source')
const connectionSrc = join(sourceRoot, 'packages/client/connection/src')
const PUBLIC_HOST = '7860-cpod-1xhxbe8kv5n4.pod.compshare.cn'
const PUBLIC_ORIGIN = `https://${PUBLIC_HOST}`
const WS_MAGIC = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === '@deepseek-ai/dsh-credentials') {
      return { url: new URL('./credentials-stub.mjs', import.meta.url).href, shortCircuit: true }
    }
    return nextResolve(specifier, context)
  },
})

const { parsePublicOrigin, publicUrlMatchesOrigin, requestTrustView, selectConfiguredPublicOrigin } = await import(
  '../../../vendor/deepseek-harness/source/packages/client/connection/src/public-origin.ts',
)
const { isTrustedApiRequest } = await import(
  '../../../vendor/deepseek-harness/source/packages/client/connection/src/api-request-trust.ts',
)
const { BrowserAuth } = await import(
  '../../../vendor/deepseek-harness/source/packages/client/connection/src/browser-auth.ts',
)

function memoryCredentials() {
  let record
  return {
    async modifyRecord(_key, mutate) {
      const next = await mutate(record)
      if (next !== undefined) record = next
      return record
    },
  }
}

function shellHtml() {
  return `<!doctype html>
<meta charset="utf-8">
<title>proxy-smoke</title>
<body><pre id="out">pending</pre>
<script type="module">
const lines = []
try {
  const described = await fetch('/api/settings/describe', {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}',
  })
  lines.push('describe ' + described.status + ' ' + await described.text())
  const youdub = await fetch('/api/youdub/v2', {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}',
  })
  lines.push('youdub ' + youdub.status + ' ' + await youdub.text())
  const wsText = await new Promise((resolve) => {
    const ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/api/remote.mux')
    const timer = setTimeout(() => resolve('timeout'), 5000)
    ws.onmessage = (event) => { clearTimeout(timer); resolve(String(event.data)); ws.close() }
    ws.onerror = () => { clearTimeout(timer); resolve('error') }
  })
  lines.push('ws ' + wsText)
} catch (error) {
  lines.push('exception ' + (error && error.message ? error.message : String(error)))
}
document.body.dataset.done = '1'
document.getElementById('out').textContent = lines.join('\\n')
</script></body>`
}

function listen(server, host = '127.0.0.1') {
  return new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, host, () => resolve(server.address().port))
  })
}

function closeServer(server) {
  return new Promise((resolve, reject) => {
    server.close((error) => { if (error) reject(error); else resolve() })
  })
}

async function startHost(originText = PUBLIC_ORIGIN) {
  const publicOrigin = parsePublicOrigin(originText, 'publicOrigin')
  const auth = await BrowserAuth.create({}, memoryCredentials(), 30)
  const server = createServer((req, res) => {
    try {
    const url = new URL(req.url ?? '/', 'http://127.0.0.1')
    const view = requestTrustView(req, { publicOrigin, trustedHosts: [], listenerProtocol: 'http:' })
    if (url.pathname === '/' || url.pathname === '/index.html') {
      const ok = auth.authorizeIndex({ method: req.method, url: req.url, headers: view.headers }, res, view.secure)
      if (!ok) return
      res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' })
      res.end(shellHtml())
      return
    }
    if (url.pathname === '/connect') {
      if (!isTrustedApiRequest({ headers: view.headers }, view.trustedHosts, '127.0.0.1', view.protocol)) {
        res.writeHead(403, { 'content-type': 'text/plain; charset=utf-8' })
        res.end('forbidden')
        return
      }
      auth.authorizeConnect({ method: req.method, url: req.url, headers: view.headers }, res, view.secure, () => 'operator-1')
      return
    }
    if (url.pathname.startsWith('/api/')) {
      const trusted = isTrustedApiRequest({ headers: view.headers }, view.trustedHosts, '127.0.0.1', view.protocol)
      if (!trusted) {
        res.writeHead(403, { 'content-type': 'text/plain; charset=utf-8' })
        res.end('forbidden')
        return
      }
      const seen = { headers: view.headers }
      const allowed = auth.isAuthenticated(seen, view.secure) || auth.authenticateApi(seen, view.secure) !== undefined
      if (!allowed) {
        res.writeHead(401, { 'content-type': 'text/plain; charset=utf-8' })
        res.end('unauthorized')
        return
      }
      res.writeHead(200, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' })
      res.end(JSON.stringify({ ok: true, path: url.pathname }))
      return
    }
    res.writeHead(404)
    res.end()
    } catch (error) {
      console.error('handler', error)
      if (!res.headersSent) res.writeHead(500, { 'content-type': 'text/plain' })
      res.end(error instanceof Error ? error.stack : String(error))
    }
  })
  server.on('upgrade', (req, socket) => {
    try {
    const view = requestTrustView(req, { publicOrigin, trustedHosts: [], listenerProtocol: 'http:' })
    const trusted = isTrustedApiRequest({ headers: view.headers }, view.trustedHosts, '127.0.0.1', view.protocol)
    const seen = { headers: view.headers }
    const allowed = trusted && (auth.isAuthenticated(seen, view.secure) || auth.authenticateApi(seen, view.secure) !== undefined)
    if (!allowed) {
      const status = trusted ? 401 : 403
      const reason = status === 401 ? 'Unauthorized' : 'Forbidden'
      socket.write(`HTTP/1.1 ${status} ${reason}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n`)
      socket.destroy()
      return
    }
    const accept = createHash('sha1').update(String(req.headers['sec-websocket-key']) + WS_MAGIC).digest('base64')
    socket.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
      + `Sec-WebSocket-Accept: ${accept}\r\n\r\n`)
    const payload = Buffer.from('ready')
    const frame = Buffer.alloc(2 + payload.length)
    frame[0] = 0x81
    frame[1] = payload.length
    payload.copy(frame, 2)
    socket.write(frame)
    socket.end()
    } catch (error) {
      console.error('upgrade', error)
      socket.destroy()
    }
  })
  server.on('clientError', (error, socket) => {
    socket.end('HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n')
    console.error('clientError', error.message)
  })
  const port = await listen(server)
  const token = new URL(auth.authenticatedUrl(`http://127.0.0.1:${port}/`)).searchParams.get('token')
  return {
    port, auth, token, publicOrigin, server,
    close: () => {
      server.closeAllConnections()
      return closeServer(server)
    },
  }
}

function call({ port, path, method = 'GET', headers = {}, body }) {
  return new Promise((resolve, reject) => {
    const req = httpRequest({ hostname: '127.0.0.1', port, path, method, headers, timeout: 2000 }, (res) => {
      const chunks = []
      res.on('data', (chunk) => chunks.push(chunk))
      res.on('end', () => resolve({
        status: res.statusCode,
        headers: res.headers,
        body: Buffer.concat(chunks).toString('utf8'),
      }))
    })
    req.on('error', reject)
    req.on('timeout', () => { req.destroy(new Error(`timeout ${method} ${path}`)) })
    req.end(body)
  })
}

function cookiePair(response) {
  const raw = response.headers['set-cookie']
  const line = Array.isArray(raw) ? raw[0] : raw
  assert.equal(typeof line, 'string')
  return { line, pair: line.split(';', 1)[0] }
}

function proxyHeaders(port, extra = {}) {
  return {
    host: `127.0.0.1:${port}`,
    'x-forwarded-host': PUBLIC_HOST,
    'x-forwarded-proto': 'https',
    ...extra,
  }
}

function openWebSocket({ port, headers }) {
  return new Promise((resolve, reject) => {
    const key = randomBytes(16).toString('base64')
    const req = httpRequest({
      hostname: '127.0.0.1',
      port,
      path: '/api/remote.mux',
      headers: {
        Connection: 'Upgrade',
        Upgrade: 'websocket',
        'Sec-WebSocket-Version': '13',
        'Sec-WebSocket-Key': key,
        ...headers,
      },
    })
    const fail = (error) => { req.destroy(); reject(error) }
    const timer = setTimeout(() => fail(new Error('websocket upgrade timeout')), 2000)
    req.on('upgrade', (res, socket, head) => {
      clearTimeout(timer)
      const buf = Buffer.from(head)
      const payload = buf.length >= 2 && buf[0] === 0x81 ? buf.subarray(2, 2 + (buf[1] & 0x7f)).toString('utf8') : ''
      socket.destroy()
      resolve({ status: res.statusCode, payload })
    })
    req.on('response', (res) => {
      clearTimeout(timer)
      res.resume()
      res.on('end', () => resolve({ status: res.statusCode, payload: '' }))
    })
    req.on('error', (error) => { clearTimeout(timer); reject(error) })
    req.end()
  })
}

test('public origin parser accepts one exact host and rejects wildcards', () => {
  const origin = parsePublicOrigin(`${PUBLIC_ORIGIN}/`, 'publicOrigin')
  assert.equal(origin.origin, PUBLIC_ORIGIN)
  assert.equal(origin.protocol, 'https:')
  assert.equal(origin.hostHeader, PUBLIC_HOST)
  assert.equal(origin.trustedAuthority, `${PUBLIC_HOST}:443`)
  assert.equal(parsePublicOrigin(`${PUBLIC_ORIGIN}:443`).origin, PUBLIC_ORIGIN)
  assert.equal(selectConfiguredPublicOrigin(PUBLIC_ORIGIN, 'https://other.example').origin, PUBLIC_ORIGIN)
  assert.equal(selectConfiguredPublicOrigin(undefined, PUBLIC_ORIGIN).origin, PUBLIC_ORIGIN)
  assert.equal(selectConfiguredPublicOrigin(undefined, undefined), undefined)
  assert.equal(publicUrlMatchesOrigin(`${PUBLIC_ORIGIN}/ui`, origin), true)
  assert.equal(publicUrlMatchesOrigin('https://other.example/ui', origin), false)
  assert.throws(() => parsePublicOrigin('https://*.pod.compshare.cn'), /wildcard/)
  assert.throws(() => parsePublicOrigin(`${PUBLIC_ORIGIN}/app`), /no path/)
  assert.throws(() => parsePublicOrigin('https://user@host.example'), /credentials/)
  assert.throws(() => parsePublicOrigin('http://host.example?x=1'), /query/)
  assert.throws(() => selectConfiguredPublicOrigin(undefined, '   '), /whitespace/)
})

test('forwarded headers from a non-loopback peer do not change the fence', () => {
  const origin = parsePublicOrigin(PUBLIC_ORIGIN)
  const headers = {
    host: '127.0.0.1:3086',
    origin: PUBLIC_ORIGIN,
    'x-forwarded-host': PUBLIC_HOST,
    'x-forwarded-proto': 'https',
  }
  const remote = requestTrustView(
    { headers, socket: { remoteAddress: '203.0.113.9' } },
    { publicOrigin: origin, trustedHosts: [], listenerProtocol: 'http:' },
  )
  assert.equal(remote.proxied, false)
  assert.equal(isTrustedApiRequest({ headers: remote.headers }, remote.trustedHosts, '127.0.0.1', remote.protocol), false)
  const local = requestTrustView(
    { headers, socket: { remoteAddress: '::ffff:127.0.0.1' } },
    { publicOrigin: origin, trustedHosts: [], listenerProtocol: 'http:' },
  )
  assert.equal(local.proxied, true)
  assert.equal(local.secure, true)
  assert.equal(isTrustedApiRequest({ headers: local.headers }, local.trustedHosts, '127.0.0.1', local.protocol), true)
  const listed = requestTrustView({
    headers: { ...headers, 'x-forwarded-host': `${PUBLIC_HOST}, evil.example` },
    socket: { remoteAddress: '127.0.0.1' },
  }, { publicOrigin: origin, trustedHosts: [], listenerProtocol: 'http:' })
  assert.equal(listed.proxied, false)
})

test('host wiring keeps the origin check and publishes the exact origin', async () => {
  const rpc = await readFile(join(connectionSrc, 'rpc-host.ts'), 'utf8')
  assert.match(rpc, /requestTrustView\(/)
  assert.match(rpc, /view\.headers/)
  assert.match(rpc, /view\.secure/)
  const startup = await readFile(join(sourceRoot, 'packages/bundle/web-app/src/startup.ts'), 'utf8')
  assert.match(startup, /--public-origin/)
  assert.match(startup, /YOUDUB_PUBLIC_ORIGIN/)
  assert.match(startup, /selectConfiguredPublicOrigin/)
  const patch = await readFile(join(here, '../cordis.patch.yml'), 'utf8')
  assert.match(patch, /publicOrigin: !!js ctx\.webStartup\.publicOrigin/)
})

test('authenticated external origin reaches API and WebSocket; bad token, foreign origin, and localhost stay distinct', { timeout: 10_000 }, async () => {
  const host = await startHost()
  try {
    const exchanged = await call({
      port: host.port,
      path: `/?token=${host.token}`,
      headers: proxyHeaders(host.port),
    })
    assert.equal(exchanged.status, 303)
    const cookie = cookiePair(exchanged)
    assert.match(cookie.line, /;\s*Secure(?:;|$)/)
    assert.match(cookie.line, /HttpOnly/)
    assert.match(cookie.line, /SameSite=Strict/)
    assert.match(cookie.line, /Path=\//)

    const described = await call({
      port: host.port,
      method: 'POST',
      path: '/api/settings/describe',
      headers: proxyHeaders(host.port, {
        origin: PUBLIC_ORIGIN,
        cookie: cookie.pair,
        'sec-fetch-site': 'same-origin',
        'content-type': 'application/json',
      }),
      body: '{}',
    })
    assert.equal(described.status, 200)
    assert.deepEqual(JSON.parse(described.body), { ok: true, path: '/api/settings/describe' })

    const youdub = await call({
      port: host.port,
      method: 'POST',
      path: '/api/youdub/v2',
      headers: proxyHeaders(host.port, {
        origin: PUBLIC_ORIGIN,
        cookie: cookie.pair,
        'sec-fetch-site': 'same-origin',
        'content-type': 'application/json',
      }),
      body: '{}',
    })
    assert.equal(youdub.status, 200)
    assert.deepEqual(JSON.parse(youdub.body), { ok: true, path: '/api/youdub/v2' })

    const socket = await openWebSocket({
      port: host.port,
      headers: proxyHeaders(host.port, {
        origin: PUBLIC_ORIGIN,
        cookie: cookie.pair,
        'sec-fetch-site': 'same-origin',
      }),
    })
    assert.equal(socket.status, 101)
    assert.equal(socket.payload, 'ready')

    const missing = await call({
      port: host.port,
      method: 'POST',
      path: '/api/settings/describe',
      headers: proxyHeaders(host.port, { origin: PUBLIC_ORIGIN, 'content-type': 'application/json' }),
      body: '{}',
    })
    assert.equal(missing.status, 401)

    const wrongCookie = await call({
      port: host.port,
      method: 'POST',
      path: '/api/youdub/v2',
      headers: proxyHeaders(host.port, {
        origin: PUBLIC_ORIGIN,
        cookie: 'dsh-auth-not-a-session=nope',
        'content-type': 'application/json',
      }),
      body: '{}',
    })
    assert.equal(wrongCookie.status, 401)

    const wrongToken = await call({
      port: host.port,
      path: '/?token=not-the-launch-token',
      headers: proxyHeaders(host.port),
    })
    assert.equal(wrongToken.status, 401)
    assert.equal(wrongToken.headers['set-cookie'], undefined)

    const foreign = await call({
      port: host.port,
      method: 'POST',
      path: '/api/settings/describe',
      headers: proxyHeaders(host.port, {
        origin: 'https://evil.example',
        cookie: cookie.pair,
        'sec-fetch-site': 'cross-site',
        'content-type': 'application/json',
      }),
      body: '{}',
    })
    assert.equal(foreign.status, 403)

    const foreignSocket = await openWebSocket({
      port: host.port,
      headers: proxyHeaders(host.port, { origin: 'https://evil.example', cookie: cookie.pair }),
    })
    assert.equal(foreignSocket.status, 403)

    const unauthenticatedSocket = await openWebSocket({
      port: host.port,
      headers: proxyHeaders(host.port, { origin: PUBLIC_ORIGIN }),
    })
    assert.equal(unauthenticatedSocket.status, 401)

    const spoofedList = await call({
      port: host.port,
      method: 'POST',
      path: '/api/settings/describe',
      headers: proxyHeaders(host.port, {
        'x-forwarded-host': `${PUBLIC_HOST}, evil.example`,
        origin: PUBLIC_ORIGIN,
        cookie: cookie.pair,
      }),
      body: '{}',
    })
    assert.equal(spoofedList.status, 403)

    const loopback = await call({
      port: host.port,
      path: `/?token=${host.token}`,
      headers: { host: `127.0.0.1:${host.port}`, origin: `http://127.0.0.1:${host.port}` },
    })
    assert.equal(loopback.status, 303)
    const localCookie = cookiePair(loopback)
    assert.doesNotMatch(localCookie.line, /Secure/)
    const localApi = await call({
      port: host.port,
      method: 'POST',
      path: '/api/youdub/v2',
      headers: {
        host: `127.0.0.1:${host.port}`,
        origin: `http://127.0.0.1:${host.port}`,
        cookie: localCookie.pair,
        'sec-fetch-site': 'same-origin',
        'content-type': 'application/json',
      },
      body: '{}',
    })
    assert.equal(localApi.status, 200)
    const localSocket = await openWebSocket({
      port: host.port,
      headers: {
        host: `127.0.0.1:${host.port}`,
        origin: `http://127.0.0.1:${host.port}`,
        cookie: localCookie.pair,
        'sec-fetch-site': 'same-origin',
      },
    })
    assert.equal(localSocket.status, 101)
    assert.equal(localSocket.payload, 'ready')

    const connect = await call({
      port: host.port,
      path: `/connect?token=${host.token}`,
      headers: proxyHeaders(host.port),
    })
    assert.equal(connect.status, 302)
    const operator = cookiePair(connect)
    assert.match(operator.line, /;\s*Secure(?:;|$)/)
    const operatorSocket = await openWebSocket({
      port: host.port,
      headers: proxyHeaders(host.port, { origin: PUBLIC_ORIGIN, cookie: operator.pair, 'sec-fetch-site': 'same-origin' }),
    })
    assert.equal(operatorSocket.status, 101)
    assert.equal(operatorSocket.payload, 'ready')
  } finally {
    await host.close()
  }
})

