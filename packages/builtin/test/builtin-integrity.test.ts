import { test } from 'node:test'
import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { builtinIntegrities, reviewedIntegrities } from '../src/builtin-integrity.js'

test('execution identities ignore settings and templates but change for the affected model implementation', async () => {
  const edits = new Map<string, string>(), reads = new Set<string>()
  const read = async (path: string) => { reads.add(path); return Buffer.from(edits.get(path) ?? path) }
  const before = await builtinIntegrities('/app', read)
  edits.set('/app/apps/harness/src/backend/settings.mjs', 'unrelated settings change')
  edits.set('/app/apps/harness/src/backend/setup.mjs', 'unrelated setup change')
  edits.set('/app/packages/builtin/src/workflow-templates/reviewed-subtitles.json', 'new template')
  assert.deepEqual(await builtinIntegrities('/app', read), before)
  assert.ok(!reads.has('/app/apps/harness/src/backend/settings.mjs'))
  edits.set('/app/backend/app/v1/asr.py', 'new recognition behavior')
  const changed = await builtinIntegrities('/app', read)
  assert.notEqual(changed.whisper.integrity, before.whisper.integrity)
  for (const id of Object.keys(before).filter(id => id !== 'whisper')) assert.equal(changed[id].integrity, before[id].integrity, id)
})

test('the a270415 execution pins are reviewed for this tree and nowhere else', async () => {
  const root = fileURLToPath(new URL('../../..', import.meta.url))
  const current = await builtinIntegrities(root)
  const pinned = {
    'youdub.download': 'sha256:f9cd5624128799de3653e486e1666e70b3a06c84bfbc2b40c5305a5334edf950',
    'youdub.media': 'sha256:acecd499efebde2036412267c558d8dc0ab4d690790995218fcab2c63ab011f3',
    whisper: 'sha256:e04783bad9df83f72b801c0cdc0c40aa4cb18e4761eb5f35cb4af0e9a7a87c15',
    voxcpm: 'sha256:ef4d0a6c1df36e15d890cd2290e0fc1f0e540294a47fd40177d6221d3ba71c6f',
    demucs: 'sha256:cd94b870dfcf23872842631546825dbd13e48486408a377c6511d155df3e9526',
    qwen_forced_aligner: 'sha256:922d210897cf2df75d7e26d0e02ae6ffbd997e986e0cd50c7cd7f0d6a799375b',
  }
  for (const [id, previous] of Object.entries(pinned)) {
    assert.notEqual(current[id].integrity, previous, id)
    assert.ok(current[id].compatibleIntegrities.includes(previous), id)
    assert.deepEqual(reviewedIntegrities(id, 'sha256:' + 'ab'.repeat(32)), [])
  }
  assert.equal(current['youdub.graph'].integrity, 'sha256:abe8c08284a2089850e123937fb58d25747c5bc8405281720e79e323b998a349')
  assert.equal(current['youdub.localize'].integrity, 'sha256:1aae61ce5bc60a45e160caeec9560f47d5bc98d52efd7a9e6ddbc6eed4afb3bf')
  assert.deepEqual(current['youdub.graph'].compatibleIntegrities, [])
  assert.deepEqual(current['youdub.localize'].compatibleIntegrities, [])
})

test('a reviewed compatibility mapping expires when the current implementation changes', () => {
  const records = { provider: { integrity: 'reviewed-current', previous: ['verified-old'] } }
  assert.deepEqual(reviewedIntegrities('provider', 'reviewed-current', records), ['verified-old'])
  assert.deepEqual(reviewedIntegrities('provider', 'future-unreviewed', records), [])
  assert.deepEqual(reviewedIntegrities('other', 'reviewed-current', records), [])
})
