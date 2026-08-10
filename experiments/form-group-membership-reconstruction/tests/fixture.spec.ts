import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const experimentDirectory = new URL('../', import.meta.url)
const manifestFile = new URL('fixture-manifest.json', experimentDirectory)

const fixtureIds = [
  'person-context-semantic-first',
  'person-context-presentation-first',
  'purpose-context-semantic-first',
  'purpose-context-presentation-first',
] as const

test('v2 fixture wrappers retain their experiment and fixture identities', async () => {
  const manifest = JSON.parse(await readFile(manifestFile, 'utf8')) as {
    fixtures: Array<{ fixtureId: string; fixtureVersion: string }>
  }

  assert.deepEqual(
    manifest.fixtures.map(({ fixtureId }) => fixtureId),
    fixtureIds,
  )
  assert.deepEqual(
    [...new Set(manifest.fixtures.map(({ fixtureVersion }) => fixtureVersion))],
    ['v1'],
  )

  for (const fixtureId of fixtureIds) {
    const module = await readFile(
      new URL(`fixtures/${fixtureId}.tsx`, experimentDirectory),
      'utf8',
    )
    assert.match(
      module,
      /experimentId="form-group-membership-reconstruction"/,
    )
    assert.match(module, new RegExp(`fixtureId="${fixtureId}"`))
  }
})
