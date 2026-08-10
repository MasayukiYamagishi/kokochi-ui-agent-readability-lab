import assert from 'node:assert/strict'
import { readdir, readFile } from 'node:fs/promises'
import test from 'node:test'

const repositoryRoot = new URL('../../../', import.meta.url)
const experimentsDirectory = new URL('experiments/', repositoryRoot)

async function findExperimentFixtures() {
  let experiments
  try {
    experiments = await readdir(experimentsDirectory, { withFileTypes: true })
  } catch (error) {
    if (error?.code === 'ENOENT') {
      return []
    }
    throw error
  }

  const fixtures = []
  for (const experiment of experiments) {
    if (!experiment.isDirectory()) {
      continue
    }

    const fixtureDirectory = new URL(
      `${experiment.name}/fixtures/`,
      experimentsDirectory,
    )
    let entries
    try {
      entries = await readdir(fixtureDirectory, { withFileTypes: true })
    } catch (error) {
      if (error?.code === 'ENOENT') {
        continue
      }
      throw error
    }

    for (const entry of entries) {
      if (entry.isFile() && entry.name.endsWith('.tsx')) {
        fixtures.push({
          experimentId: experiment.name,
          fixtureId: entry.name.slice(0, -'.tsx'.length),
        })
      }
    }
  }

  return fixtures
}

test('each fixture manifest exactly declares its experiment fixture modules', async () => {
  const fixtures = await findExperimentFixtures()
  const fixturesByExperiment = new Map()

  for (const fixture of fixtures) {
    const fixtureIds = fixturesByExperiment.get(fixture.experimentId) ?? []
    fixtureIds.push(fixture.fixtureId)
    fixturesByExperiment.set(fixture.experimentId, fixtureIds)
  }

  for (const [experimentId, fixtureIds] of fixturesByExperiment) {
    const manifestFile = new URL(
      `${experimentId}/fixture-manifest.json`,
      experimentsDirectory,
    )
    const manifest = JSON.parse(await readFile(manifestFile, 'utf8'))

    assert.equal(typeof manifest.experimentTitle, 'string')
    assert.ok(manifest.experimentTitle.length > 0)
    assert.equal(typeof manifest.experimentDescription, 'string')
    assert.ok(manifest.experimentDescription.length > 0)
    assert.ok(Array.isArray(manifest.fixtures))

    const declaredFixtureIds = manifest.fixtures.map((fixture) => {
      assert.equal(typeof fixture.fixtureId, 'string')
      assert.equal(typeof fixture.fixtureVersion, 'string')
      assert.equal(typeof fixture.description, 'string')
      return fixture.fixtureId
    })

    assert.equal(
      new Set(declaredFixtureIds).size,
      declaredFixtureIds.length,
      `Duplicate fixture ID in ${experimentId}/fixture-manifest.json`,
    )
    assert.deepEqual(declaredFixtureIds.toSorted(), fixtureIds.toSorted())
  }
})
