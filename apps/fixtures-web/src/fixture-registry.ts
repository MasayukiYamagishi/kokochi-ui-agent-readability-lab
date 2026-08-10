import type { ComponentType } from 'react'
import { fixturePath } from './fixture-route'

const IDENTIFIER_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/
const EXPERIMENT_FIXTURE_SOURCE_PATTERN =
  /^\.\.\/\.\.\/\.\.\/experiments\/([^/]+)\/fixtures\/([^/]+)\.tsx$/
const EXPERIMENT_FIXTURE_MANIFEST_PATTERN =
  /^\.\.\/\.\.\/\.\.\/experiments\/([^/]+)\/fixture-manifest\.json$/

type FixtureModule = {
  default: ComponentType
}

type FixtureMetadata = {
  experimentId: string
  experimentTitle: string
  experimentDescription: string
  fixtureId: string
  fixtureVersion: string
  description: string
  sourcePath: string
}

export type FixtureRegistration = FixtureMetadata & {
  load: () => Promise<FixtureModule>
  route: string
}

const experimentFixtureModules = import.meta.glob<FixtureModule>(
  '../../../experiments/*/fixtures/*.tsx',
)
const experimentFixtureManifests = import.meta.glob<unknown>(
  '../../../experiments/*/fixture-manifest.json',
  { eager: true, import: 'default' },
)

function requireRecord(
  value: unknown,
  fieldName: string,
): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error(`${fieldName} must be an object`)
  }

  return value as Record<string, unknown>
}

function requireNonEmptyString(value: unknown, fieldName: string): string {
  if (typeof value !== 'string' || value.trim() === '') {
    throw new Error(`${fieldName} must be a non-empty string`)
  }

  return value
}

function assertIdentifier(value: string, fieldName: string): void {
  if (!IDENTIFIER_PATTERN.test(value)) {
    throw new Error(`${fieldName} must be a kebab-case identifier: ${value}`)
  }
}

function createRegistration(
  metadata: FixtureMetadata,
  load: () => Promise<FixtureModule>,
  allowTestSource = false,
): FixtureRegistration {
  assertIdentifier(metadata.experimentId, 'experimentId')
  assertIdentifier(metadata.fixtureId, 'fixtureId')
  assertIdentifier(metadata.fixtureVersion, 'fixtureVersion')

  if (!allowTestSource) {
    const sourceMatch = metadata.sourcePath.match(
      EXPERIMENT_FIXTURE_SOURCE_PATTERN,
    )
    if (!sourceMatch) {
      throw new Error(
        `Fixture source must use experiments/<experiment-id>/fixtures/<fixture-id>.tsx: ${metadata.sourcePath}`,
      )
    }
    if (
      sourceMatch[1] !== metadata.experimentId ||
      sourceMatch[2] !== metadata.fixtureId
    ) {
      throw new Error(
        `Fixture identifiers must match its source path: ${metadata.sourcePath}`,
      )
    }
  }

  return {
    ...metadata,
    load,
    route: fixturePath(metadata.experimentId, metadata.fixtureId),
  }
}

function createExperimentRegistration(
  metadata: FixtureMetadata,
): FixtureRegistration {
  const load = experimentFixtureModules[metadata.sourcePath]
  if (!load) {
    throw new Error(
      `Registered fixture source does not exist or is outside the canonical fixture path: ${metadata.sourcePath}`,
    )
  }

  return createRegistration(metadata, load)
}

function createExperimentRegistrations(
  manifestSourcePath: string,
  manifestValue: unknown,
): readonly FixtureRegistration[] {
  const manifestSourceMatch = manifestSourcePath.match(
    EXPERIMENT_FIXTURE_MANIFEST_PATTERN,
  )
  if (!manifestSourceMatch) {
    throw new Error(
      `Fixture manifest must use experiments/<experiment-id>/fixture-manifest.json: ${manifestSourcePath}`,
    )
  }

  const experimentId = manifestSourceMatch[1]
  assertIdentifier(experimentId, 'experimentId')

  const manifest = requireRecord(manifestValue, manifestSourcePath)
  const experimentTitle = requireNonEmptyString(
    manifest.experimentTitle,
    `${manifestSourcePath}.experimentTitle`,
  )
  const experimentDescription = requireNonEmptyString(
    manifest.experimentDescription,
    `${manifestSourcePath}.experimentDescription`,
  )
  if (!Array.isArray(manifest.fixtures) || manifest.fixtures.length === 0) {
    throw new Error(`${manifestSourcePath}.fixtures must be a non-empty array`)
  }

  return manifest.fixtures.map((fixtureValue, index) => {
    const fieldName = `${manifestSourcePath}.fixtures[${index}]`
    const fixture = requireRecord(fixtureValue, fieldName)
    const fixtureId = requireNonEmptyString(
      fixture.fixtureId,
      `${fieldName}.fixtureId`,
    )
    const fixtureVersion = requireNonEmptyString(
      fixture.fixtureVersion,
      `${fieldName}.fixtureVersion`,
    )
    const description = requireNonEmptyString(
      fixture.description,
      `${fieldName}.description`,
    )
    const sourcePath =
      `../../../experiments/${experimentId}/fixtures/${fixtureId}.tsx`

    return createExperimentRegistration({
      experimentId,
      experimentTitle,
      experimentDescription,
      fixtureId,
      fixtureVersion,
      description,
      sourcePath,
    })
  })
}

function createRegistry(
  registrations: readonly FixtureRegistration[],
): readonly FixtureRegistration[] {
  const routes = new Set<string>()
  const experiments = new Map<
    string,
    Pick<FixtureRegistration, 'experimentTitle' | 'experimentDescription'>
  >()

  for (const registration of registrations) {
    if (routes.has(registration.route)) {
      throw new Error(`Duplicate fixture route: ${registration.route}`)
    }
    routes.add(registration.route)

    const experiment = experiments.get(registration.experimentId)
    if (
      experiment &&
      (experiment.experimentTitle !== registration.experimentTitle ||
        experiment.experimentDescription !== registration.experimentDescription)
    ) {
      throw new Error(
        `Conflicting metadata for experiment: ${registration.experimentId}`,
      )
    }
    experiments.set(registration.experimentId, {
      experimentTitle: registration.experimentTitle,
      experimentDescription: registration.experimentDescription,
    })
  }

  return registrations
}

const experimentFixtures = Object.entries(experimentFixtureManifests).flatMap(
  ([manifestSourcePath, manifest]) =>
    createExperimentRegistrations(manifestSourcePath, manifest),
)

const testFixtures: readonly FixtureRegistration[] =
  import.meta.env.DEV || import.meta.env.MODE === 'fixture-test'
    ? [
        createRegistration(
          {
            experimentId: 'fixture-host-contract',
            experimentTitle: 'Fixture host contract',
            experimentDescription:
              'Test-only registrations used to verify routing and isolation.',
            fixtureId: 'catalog-first',
            fixtureVersion: 'v1',
            description:
              'Keeps selection tests independent from registry ordering.',
            sourcePath: './testing/CatalogFirstFixture.tsx',
          },
          () => import('./testing/CatalogFirstFixture'),
          true,
        ),
        createRegistration(
          {
            experimentId: 'fixture-host-contract',
            experimentTitle: 'Fixture host contract',
            experimentDescription:
              'Test-only registrations used to verify routing and isolation.',
            fixtureId: 'stable-url',
            fixtureVersion: 'v1',
            description:
              'Confirms direct fixture URLs and required data attributes.',
            sourcePath: './testing/HostContractFixture.tsx',
          },
          () => import('./testing/HostContractFixture'),
          true,
        ),
      ]
    : []

export const fixtureRegistry = createRegistry([
  ...experimentFixtures,
  ...testFixtures,
])
