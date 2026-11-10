import test from 'node:test'
import assert from 'node:assert/strict'
import { fork } from 'node:child_process'
import { once } from 'node:events'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { prepareSourceDevelopment } from '../prepare-source-development.mjs'
import { prepareYouDubProfile } from '../prepare-profile.mjs'
import { desktopNodeEnvironment } from '../../../vendor/deepseek-harness/source/apps/desktop/src/node-environment.ts'

const app = resolve(import.meta.dirname, '..')
const root = resolve(app, '../..')
const translationEnv = {
  YOUDUB_MEDIA_ROOT: root,
  YOUDUB_TRANSLATION_MODEL_DIR: '/var/lib/youdub/models/Index-Translate-2B',
  YOUDUB_TRANSLATION_PYTHON: 'python3',
  YOUDUB_LOCAL_TRANSLATION_KEY: 'local',
}

test('official prepare packages local translation and the ESM loader activates the pi provider', { timeout: 180_000 }, async (t) => {
  const directory = await mkdtemp(join(tmpdir(), 'youdub-packaged-translation-'))
  let child
  let exited
  t.after(async () => {
    try {
      if (child?.connected) child.send({ type: 'shutdown' })
      if (exited) {
        const timeout = setTimeout(() => child.kill('SIGKILL'), 10_000)
        try { await exited } finally { clearTimeout(timeout) }
      }
    } finally { await rm(directory, { recursive: true, force: true }) }
  })

  const { project, pluginRoot, node, pnpm, nodeBin } = await prepareSourceDevelopment({ projectDirectory: join(directory, 'runtime') })
  const packaged = join(pluginRoot, 'dist/local-translation.cjs')
  const hostBundle = await readFile(join(pluginRoot, 'dist/host.mjs'), 'utf8')
  assert.match(hostBundle, /local-translation\.cjs/)
  const loaded = createRequire(join(pluginRoot, 'dist/host.mjs'))('./local-translation.cjs')
  assert.equal(typeof loaded.providers, 'function')
  assert.equal(loaded, createRequire(packaged)(packaged))
  const hostModule = await import(pathToFileURL(join(pluginRoot, 'dist/host.mjs')).href)
  assert.equal(hostModule.name, 'youdub-subtitles')

  const hostRequire = createRequire(join(project, 'node_modules/@deepseek-ai/dsh-desktop-host/package.json'))
  const loaderPath = hostRequire.resolve('@deepseek-ai/cordis-plugin-loader')
  assert.match(loaderPath, /vendor\/loader\/lib\/index\.js$/)
  const { interpolate } = await import(pathToFileURL(loaderPath).href)
  const includePath = hostRequire.resolve('@deepseek-ai/cordis-plugin-include')
  const { entryListSchema } = await import(pathToFileURL(includePath).href)
  const yaml = createRequire(includePath)('js-yaml')
  const patch = yaml.load(await readFile(join(pluginRoot, 'cordis.patch.yml'), 'utf8'), { schema: entryListSchema })
  const expression = patch[0].insert.find(entry => entry.id === 'llm-pi-ai').config.providers
  assert.equal(typeof expression.__jsExpr, 'string')
  assert.doesNotMatch(expression.__jsExpr, /\brequire\s*\(/)
  const bare = Object.create(null)
  assert.throws(() => interpolate(bare, { __jsExpr: 'require("node:module")' }), /require is not defined/)
  const previous = { ...process.env }
  Object.assign(process.env, translationEnv)
  let providers
  const cwd = process.cwd()
  try {
    process.chdir(project)
    providers = interpolate(bare, expression)
  } finally {
    process.chdir(cwd)
    for (const key of Object.keys(translationEnv)) {
      if (previous[key] === undefined) delete process.env[key]
      else process.env[key] = previous[key]
    }
  }
  assert.equal(providers.local.baseURL, 'http://127.0.0.1:8091/v1')
  assert.equal(providers.local.api, 'openai-completions')
  assert.equal(providers.local.models[0].id, 'IndexTeam/Index-Translate-2B')

  const cordis = await import(pathToFileURL(hostRequire.resolve('@deepseek-ai/cordis')).href)
  const llm = await import(pathToFileURL(hostRequire.resolve('@deepseek-ai/dsh-llm')).href)
  const pi = await import(pathToFileURL(hostRequire.resolve('@deepseek-ai/dsh-llm-pi-ai')).href)
  const ctx = new cordis.Context()
  t.after(() => ctx.fiber.dispose())
  await ctx.plugin(llm.default)
  await ctx.plugin(pi, { providers })
  await ctx.fiber.await()
  assert.ok(ctx.llm.listProviders().some(item => item.id === 'local'))

  const home = join(directory, 'home')
  const profile = join(home, 'profiles/youdub')
  await prepareYouDubProfile(profile, pluginRoot, project, { command: node, args: ['--expose-internals', pnpm], env: desktopNodeEnvironment(node, nodeBin, process.env) })
  child = fork(join(project, 'node_modules/@deepseek-ai/dsh-desktop-host/lib/index.js'),
    [project, profile, pnpm, nodeBin], {
      cwd: project, execPath: node, execArgv: ['--expose-internals'], silent: true,
      env: { ...desktopNodeEnvironment(node, undefined, process.env), ...translationEnv, DSH_HOME: home, YOUDUB_NODE_PATH: node, YOUDUB_TEST_SKIP_SETUP: '1', YOUDUB_TEST_PYTHON: process.env.YOUDUB_TEST_PYTHON || '/usr/bin/python3' },
    })
  let output = ''
  child.stderr.on('data', data => { output = (output + data).slice(-24_000) })
  child.stdout.on('data', data => { output = (output + data).slice(-24_000) })
  exited = once(child, 'exit')
  const ready = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`Host did not become ready: ${output}`)), 90_000)
    child.on('message', message => {
      if (message.type === 'fatal') { clearTimeout(timer); reject(new Error(message.diagnostic || message.message || output)) }
      if (message.type === 'ready') { clearTimeout(timer); resolve(message) }
    })
  })
  assert.doesNotMatch(output, /require is not defined/)
  assert.doesNotMatch(output, /MODULE_NOT_FOUND/)
  assert.doesNotMatch(output, /did not activate/)
  const origin = new URL(ready.url).origin
  const anonymous = await fetch(`${origin}/api/youdub/v2`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ method: 'GET', path: '/api/setup' }) })
  assert.equal(anonymous.status, 401)
  const exchange = await fetch(ready.url, { redirect: 'manual' })
  assert.equal(exchange.status, 303)
  const cookie = exchange.headers.getSetCookie().map(value => value.split(';')[0]).join('; ')
  const tasks = await fetch(`${origin}/api/youdub/v2`, { method: 'POST', headers: { 'content-type': 'application/json', cookie }, body: JSON.stringify({ method: 'GET', path: '/api/v2/tasks' }) })
  assert.equal(tasks.status, 200, await tasks.text())
})
