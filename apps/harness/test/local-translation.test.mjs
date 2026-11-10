import test from 'node:test'
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { createRequire } from 'node:module'

const { providers, openLocalTranslation, resetLocalTranslation, localTranslationStatus, MODEL_ID } = createRequire(import.meta.url)(
  '../src/backend/local-translation.cjs',
)

test('a model directory selects Index-Translate-2B on loopback and rejects a public URL', () => {
  const profile = providers({ YOUDUB_TRANSLATION_MODEL_DIR: '/var/lib/youdub/models/Index-Translate-2B' })
  assert.equal(profile.local.models[0].id, MODEL_ID)
  assert.equal(profile.local.baseURL, 'http://127.0.0.1:8091/v1')
  assert.equal(profile.local.models[0].maxTokens, 1024)
  assert.equal(profile.local.models[0].contextWindow, 8192)
  assert.throws(() => providers({
    YOUDUB_TRANSLATION_BASE_URL: 'https://api.openai.com/v1',
    YOUDUB_TRANSLATION_MODEL: MODEL_ID,
  }), /loopback/)
})

test('one holder stopping does not unload the server still used by another task', async () => {
  resetLocalTranslation()
  const server = createServer((_req, res) => { res.end('ok') })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const port = server.address().port
  const env = {
    YOUDUB_TRANSLATION_BASE_URL: `http://127.0.0.1:${port}/v1`,
    YOUDUB_TRANSLATION_MODEL: MODEL_ID,
    YOUDUB_TRANSLATION_SERVER_COMMAND: `node -e "process.on('SIGTERM',()=>process.exit(0)); setInterval(()=>{},1000)"`,
  }
  try {
    const first = await openLocalTranslation(env, 2000, 80)
    const second = await openLocalTranslation(env, 2000, 80)
    assert.equal(first.started && second.started, true)
    assert.equal(localTranslationStatus().holders, 2)
    await first.stop()
    await new Promise(resolve => setTimeout(resolve, 150))
    assert.equal(localTranslationStatus().alive, true)
    assert.equal(localTranslationStatus().holders, 1)
    await second.stop()
    await new Promise(resolve => setTimeout(resolve, 200))
    assert.equal(localTranslationStatus().alive, false)
  } finally {
    resetLocalTranslation()
    server.close()
  }
})
