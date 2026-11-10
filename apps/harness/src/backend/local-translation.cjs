/** Loopback Index-Translate route. Weights are never downloaded here. */

const { spawn } = require('node:child_process')
const net = require('node:net')

const LOOPBACK = new Set(['127.0.0.1', 'localhost', '::1'])
const MODEL_ID = 'IndexTeam/Index-Translate-2B'
const state = { holders: 0, child: null, starting: null, grace: null }

function fail(message) {
  const error = new Error(message)
  error.code = 'INVALID_CONFIG'
  return error
}

function providers(env) {
  const directory = String(env.YOUDUB_TRANSLATION_MODEL_DIR || '').trim()
  const model = String(env.YOUDUB_TRANSLATION_MODEL || MODEL_ID).trim()
  const urlText = String(env.YOUDUB_TRANSLATION_BASE_URL || (directory ? 'http://127.0.0.1:8091/v1' : '')).trim()
  if (!urlText || !model) throw fail('YOUDUB_TRANSLATION_BASE_URL and YOUDUB_TRANSLATION_MODEL must both be set.')
  let url
  try { url = new URL(urlText) } catch { throw fail('YOUDUB_TRANSLATION_BASE_URL is not a URL.') }
  if (!LOOPBACK.has(url.hostname)) throw fail('YOUDUB_TRANSLATION_BASE_URL must be a loopback address.')
  if (directory && !directory.startsWith('/')) throw fail('YOUDUB_TRANSLATION_MODEL_DIR must be an absolute path.')
  const maxTokens = Number(env.YOUDUB_TRANSLATION_MAX_TOKENS || 1024)
  const contextWindow = Number(env.YOUDUB_TRANSLATION_CONTEXT || 8192)
  if (!Number.isInteger(maxTokens) || maxTokens < 256 || !Number.isInteger(contextWindow) || contextWindow < maxTokens) {
    throw fail('YOUDUB_TRANSLATION_MAX_TOKENS must be an integer >= 256 and not larger than the context window.')
  }
  if (env === process.env && !process.env.YOUDUB_LOCAL_TRANSLATION_KEY) process.env.YOUDUB_LOCAL_TRANSLATION_KEY = 'local'
  return {
    local: {
      displayName: 'Index-Translate-2B',
      api: 'openai-completions',
      baseURL: urlText,
      apiKeyEnv: 'YOUDUB_LOCAL_TRANSLATION_KEY',
      models: [{ id: model, name: model, contextWindow, maxTokens, input: ['text'] }],
      compat: { maxTokensField: 'max_tokens' },
    },
  }
}

function quote(value) {
  return `'${String(value).replaceAll("'", `'"'"'`)}'`
}

function commandFor(env) {
  const cwd = String(env.YOUDUB_MEDIA_ROOT || process.cwd())
  const explicit = String(env.YOUDUB_TRANSLATION_SERVER_COMMAND || '').trim()
  if (explicit) return { command: explicit, cwd }
  const directory = String(env.YOUDUB_TRANSLATION_MODEL_DIR || '').trim()
  if (!directory) return null
  const python = String(env.YOUDUB_TRANSLATION_PYTHON || 'python3')
  const port = new URL(providers(env).local.baseURL).port
  return {
    command: `${quote(python)} -m backend.app.v1.index_translate_server --model-dir ${quote(directory)} --host 127.0.0.1 --port ${port}`,
    cwd,
  }
}

function endpoint(env) {
  const url = new URL(providers(env).local.baseURL)
  return { host: url.hostname === 'localhost' ? '127.0.0.1' : url.hostname, port: Number(url.port || 80) }
}

function waitForPort(env, child, timeoutMs) {
  const { host, port } = endpoint(env)
  const started = Date.now()
  return new Promise((resolve, reject) => {
    const attempt = () => {
      if (child.exitCode !== null) {
        reject(fail('Local translation server exited before it accepted connections.'))
        return
      }
      if (Date.now() - started > timeoutMs) {
        reject(fail('Local translation server did not accept connections in time.'))
        return
      }
      const socket = net.connect({ host, port }, () => { socket.end(); resolve() })
      socket.on('error', () => { socket.destroy(); setTimeout(attempt, 50) })
    }
    attempt()
  })
}

function resetLocalTranslation() {
  if (state.grace) clearTimeout(state.grace)
  if (state.child && state.child.exitCode === null) state.child.kill('SIGKILL')
  state.holders = 0
  state.child = null
  state.starting = null
  state.grace = null
}

async function openLocalTranslation(env = process.env, timeoutMs = 120000, graceMs = 1000) {
  const planned = commandFor(env)
  if (!planned) return { started: false, async stop() {} }
  const { command, cwd } = planned
  providers(env)
  state.holders += 1
  if (state.grace) { clearTimeout(state.grace); state.grace = null }
  if (!state.child || state.child.exitCode !== null) {
    if (!state.starting) {
      const child = spawn(command, { shell: true, cwd, env: { ...process.env, ...env }, stdio: 'ignore' })
      state.child = child
      state.starting = waitForPort(env, child, timeoutMs).catch(error => {
        child.kill('SIGKILL')
        state.child = null
        throw error
      }).finally(() => { state.starting = null })
    }
    try { await state.starting }
    catch (error) { state.holders -= 1; throw error }
  }
  let released = false
  return {
    started: true,
    async stop() {
      if (released) return
      released = true
      state.holders = Math.max(0, state.holders - 1)
      if (state.holders > 0) return
      state.grace = setTimeout(() => {
        state.grace = null
        if (state.holders === 0 && state.child && state.child.exitCode === null) {
          state.child.kill('SIGTERM')
          state.child = null
        }
      }, graceMs)
    },
  }
}

function localTranslationStatus() {
  return { holders: state.holders, alive: Boolean(state.child && state.child.exitCode === null) }
}

module.exports = { MODEL_ID, providers, openLocalTranslation, resetLocalTranslation, localTranslationStatus }
