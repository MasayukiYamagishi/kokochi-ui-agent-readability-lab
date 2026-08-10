import type { FixtureRegistration } from './fixture-registry'

export type FixtureRoute =
  | { kind: 'catalog'; experimentId?: string }
  | { kind: 'fixture'; fixture: FixtureRegistration }
  | { kind: 'not-found'; pathname: string; reason: string }

function normalizePathname(pathname: string): string {
  if (pathname === '/') {
    return pathname
  }

  return pathname.replace(/\/+$/, '') || '/'
}

export function experimentPath(experimentId: string): string {
  return `/experiments/${experimentId}`
}

export function fixturePath(experimentId: string, fixtureId: string): string {
  return `${experimentPath(experimentId)}/${fixtureId}`
}

export function resolveFixtureRoute(
  pathname: string,
  fixtures: readonly FixtureRegistration[],
): FixtureRoute {
  const normalizedPathname = normalizePathname(pathname)
  if (normalizedPathname === '/' || normalizedPathname === '/experiments') {
    return { kind: 'catalog' }
  }

  const segments = normalizedPathname.split('/').filter(Boolean)
  if (segments.at(0) !== 'experiments') {
    return {
      kind: 'not-found',
      pathname: normalizedPathname,
      reason: 'Page not found',
    }
  }

  if (segments.length === 2) {
    const experimentId = segments[1]
    if (fixtures.some((fixture) => fixture.experimentId === experimentId)) {
      return { kind: 'catalog', experimentId }
    }

    return {
      kind: 'not-found',
      pathname: normalizedPathname,
      reason: 'Experiment not found',
    }
  }

  if (segments.length === 3) {
    const fixture = fixtures.find(
      (candidate) => candidate.route === normalizedPathname,
    )
    if (fixture) {
      return { kind: 'fixture', fixture }
    }

    return {
      kind: 'not-found',
      pathname: normalizedPathname,
      reason: 'Fixture not found',
    }
  }

  return {
    kind: 'not-found',
    pathname: normalizedPathname,
    reason: 'Page not found',
  }
}
