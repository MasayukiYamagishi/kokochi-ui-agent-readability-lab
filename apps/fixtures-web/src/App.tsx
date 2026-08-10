import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type PointerEvent,
} from 'react'
import './App.css'
import { fixtureRegistry, type FixtureRegistration } from './fixture-registry'
import {
  experimentPath,
  resolveFixtureRoute,
  type FixtureRoute,
} from './fixture-route'

type ExperimentSummary = {
  id: string
  title: string
  description: string
  fixtures: readonly FixtureRegistration[]
}

type FixtureCatalogProps = {
  experimentId?: string
}

type ResizeStart = {
  pointerId: number
  clientX: number
  navigationWidth: number
}

const DEFAULT_NAVIGATION_WIDTH = 336
const MIN_NAVIGATION_WIDTH = 240
const MAX_NAVIGATION_WIDTH = 560
const NAVIGATION_RESIZE_STEP = 24

function clampNavigationWidth(width: number): number {
  return Math.min(
    MAX_NAVIGATION_WIDTH,
    Math.max(MIN_NAVIGATION_WIDTH, width),
  )
}

function groupExperiments(
  fixtures: readonly FixtureRegistration[],
): readonly ExperimentSummary[] {
  const experiments = new Map<string, ExperimentSummary>()

  for (const fixture of fixtures) {
    const existing = experiments.get(fixture.experimentId)
    if (existing) {
      experiments.set(fixture.experimentId, {
        ...existing,
        fixtures: [...existing.fixtures, fixture],
      })
      continue
    }

    experiments.set(fixture.experimentId, {
      id: fixture.experimentId,
      title: fixture.experimentTitle,
      description: fixture.experimentDescription,
      fixtures: [fixture],
    })
  }

  return [...experiments.values()]
}

function FixtureCatalog({ experimentId }: FixtureCatalogProps) {
  const experiments = useMemo(() => groupExperiments(fixtureRegistry), [])
  const visibleExperiments = experimentId
    ? experiments.filter((experiment) => experiment.id === experimentId)
    : experiments
  const visibleFixtures = visibleExperiments.flatMap(
    (experiment) => experiment.fixtures,
  )
  const [selectedRoute, setSelectedRoute] = useState(
    visibleFixtures.at(0)?.route ?? null,
  )
  const [navigationWidth, setNavigationWidth] = useState(
    DEFAULT_NAVIGATION_WIDTH,
  )
  const [isNavigationOpen, setIsNavigationOpen] = useState(true)
  const [expandedExperimentIds, setExpandedExperimentIds] = useState<
    ReadonlySet<string>
  >(() => new Set())
  const resizeStart = useRef<ResizeStart | null>(null)
  const selectedFixture =
    visibleFixtures.find((fixture) => fixture.route === selectedRoute) ??
    visibleFixtures.at(0) ??
    null
  const heading = experimentId
    ? (visibleExperiments.at(0)?.title ?? experimentId)
    : 'Fixture catalog'
  const description = experimentId
    ? (visibleExperiments.at(0)?.description ?? '')
    : 'Browse registered experiments and inspect each fixture in an isolated preview.'
  const workspaceStyle = {
    '--fixture-navigation-width': isNavigationOpen
      ? `${navigationWidth}px`
      : '0px',
  } as CSSProperties

  function resizeNavigationBy(delta: number): void {
    setNavigationWidth((currentWidth) =>
      clampNavigationWidth(currentWidth + delta),
    )
  }

  function handleResizeStart(event: PointerEvent<HTMLDivElement>): void {
    if (!isNavigationOpen || event.button !== 0) {
      return
    }

    event.preventDefault()
    event.currentTarget.setPointerCapture(event.pointerId)
    resizeStart.current = {
      pointerId: event.pointerId,
      clientX: event.clientX,
      navigationWidth,
    }
  }

  function handleResizeMove(event: PointerEvent<HTMLDivElement>): void {
    const start = resizeStart.current
    if (!start || start.pointerId !== event.pointerId) {
      return
    }

    setNavigationWidth(
      clampNavigationWidth(
        start.navigationWidth + event.clientX - start.clientX,
      ),
    )
  }

  function handleResizeEnd(event: PointerEvent<HTMLDivElement>): void {
    if (resizeStart.current?.pointerId !== event.pointerId) {
      return
    }

    resizeStart.current = null
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
  }

  function handleResizeKeyDown(event: KeyboardEvent<HTMLDivElement>): void {
    if (!isNavigationOpen || event.target !== event.currentTarget) {
      return
    }

    switch (event.key) {
      case 'ArrowLeft':
        event.preventDefault()
        resizeNavigationBy(-NAVIGATION_RESIZE_STEP)
        break
      case 'ArrowRight':
        event.preventDefault()
        resizeNavigationBy(NAVIGATION_RESIZE_STEP)
        break
      case 'Home':
        event.preventDefault()
        setNavigationWidth(MIN_NAVIGATION_WIDTH)
        break
      case 'End':
        event.preventDefault()
        setNavigationWidth(MAX_NAVIGATION_WIDTH)
        break
    }
  }

  function toggleNavigation(): void {
    setIsNavigationOpen((currentValue) => !currentValue)
  }

  function toggleExperiment(experimentIdToToggle: string): void {
    setExpandedExperimentIds((currentIds) => {
      const nextIds = new Set(currentIds)
      if (nextIds.has(experimentIdToToggle)) {
        nextIds.delete(experimentIdToToggle)
      } else {
        nextIds.add(experimentIdToToggle)
      }
      return nextIds
    })
  }

  return (
    <div className="fixture-host">
      <header className="fixture-host__header">
        <div className="fixture-host__header-copy">
          <p className="fixture-host__eyebrow">KOKOCHI UI readability lab</p>
          <h1>{heading}</h1>
          <p>{description}</p>
        </div>
        <div className="fixture-host__header-actions">
          {experimentId ? (
            <a className="fixture-host__back-link" href="/experiments">
              <span aria-hidden="true">←</span>
              Back to all experiments
            </a>
          ) : null}
          <button
            type="button"
            className="fixture-host__mobile-navigation-toggle"
            aria-controls="fixture-navigation"
            aria-expanded={isNavigationOpen}
            onClick={toggleNavigation}
          >
            {isNavigationOpen ? 'Hide sidebar' : 'Show sidebar'}
          </button>
        </div>
      </header>

      <div className="fixture-host__workspace" style={workspaceStyle}>
        <nav
          id="fixture-navigation"
          className="fixture-host__navigation"
          aria-label="Fixture navigation"
          hidden={!isNavigationOpen}
        >
          {visibleExperiments.length === 0 ? (
            <div className="fixture-host__empty">
              <h2>No fixtures registered</h2>
              <p>
                Add an experiment fixture and register it to make it available
                here.
              </p>
            </div>
          ) : (
            visibleExperiments.map((experiment) => {
              const isExpanded = expandedExperimentIds.has(experiment.id)
              const contentId = `fixture-experiment-${experiment.id}`

              return (
                <section
                  key={experiment.id}
                  className="fixture-host__experiment"
                >
                  <div className="fixture-host__experiment-heading">
                    <div>
                      <h2>
                        <button
                          type="button"
                          className="fixture-host__experiment-toggle"
                          aria-controls={contentId}
                          aria-expanded={isExpanded}
                          onClick={() => toggleExperiment(experiment.id)}
                        >
                          <span>{experiment.title}</span>
                          <span
                            className="fixture-host__experiment-indicator"
                            aria-hidden="true"
                          >
                            {isExpanded ? '▾' : '▸'}
                          </span>
                        </button>
                      </h2>
                      <code>{experiment.id}</code>
                    </div>
                    <a href={experimentPath(experiment.id)}>Overview</a>
                  </div>
                  <div
                    id={contentId}
                    className="fixture-host__experiment-content"
                    hidden={!isExpanded}
                  >
                    <p className="fixture-host__experiment-description">
                      {experiment.description}
                    </p>
                    <ul>
                      {experiment.fixtures.map((fixture) => (
                        <li key={fixture.route}>
                          <button
                            type="button"
                            className={
                              fixture.route === selectedFixture?.route
                                ? 'fixture-host__fixture fixture-host__fixture--selected'
                                : 'fixture-host__fixture'
                            }
                            onClick={() => setSelectedRoute(fixture.route)}
                            aria-pressed={
                              fixture.route === selectedFixture?.route
                            }
                          >
                            <span>
                              <strong>{fixture.fixtureId}</strong>
                              <small>Version {fixture.fixtureVersion}</small>
                            </span>
                            <span>{fixture.description}</span>
                          </button>
                          <a
                            className="fixture-host__direct-link"
                            href={fixture.route}
                            data-direct-fixture-link
                            data-experiment-id={fixture.experimentId}
                            data-fixture-id={fixture.fixtureId}
                            data-fixture-version={fixture.fixtureVersion}
                          >
                            Open direct URL
                          </a>
                        </li>
                      ))}
                    </ul>
                  </div>
                </section>
              )
            })
          )}
        </nav>

        <div
          className={
            isNavigationOpen
              ? 'fixture-host__splitter'
              : 'fixture-host__splitter fixture-host__splitter--collapsed'
          }
          role="separator"
          aria-label="Resize fixture navigation"
          aria-controls="fixture-navigation"
          aria-orientation="vertical"
          aria-valuemin={MIN_NAVIGATION_WIDTH}
          aria-valuemax={MAX_NAVIGATION_WIDTH}
          aria-valuenow={navigationWidth}
          tabIndex={isNavigationOpen ? 0 : -1}
          onDoubleClick={() => setNavigationWidth(DEFAULT_NAVIGATION_WIDTH)}
          onKeyDown={handleResizeKeyDown}
          onPointerDown={handleResizeStart}
          onPointerMove={handleResizeMove}
          onPointerUp={handleResizeEnd}
          onPointerCancel={handleResizeEnd}
          onLostPointerCapture={() => {
            resizeStart.current = null
          }}
        >
          <button
            type="button"
            className="fixture-host__splitter-toggle"
            aria-controls="fixture-navigation"
            aria-expanded={isNavigationOpen}
            aria-label={
              isNavigationOpen
                ? 'Hide fixture navigation'
                : 'Show fixture navigation'
            }
            title={
              isNavigationOpen
                ? 'Hide fixture navigation'
                : 'Show fixture navigation'
            }
            onPointerDown={(event) => event.stopPropagation()}
            onClick={toggleNavigation}
          >
            <span aria-hidden="true">{isNavigationOpen ? '‹' : '›'}</span>
          </button>
        </div>

        <main className="fixture-host__preview">
          {selectedFixture ? (
            <>
              <div className="fixture-host__preview-heading">
                <div>
                  <p>Isolated preview</p>
                  <h2>{selectedFixture.fixtureId}</h2>
                </div>
                <code>{selectedFixture.route}</code>
              </div>
              <iframe
                key={selectedFixture.route}
                title={`Preview: ${selectedFixture.fixtureId}`}
                src={selectedFixture.route}
              />
            </>
          ) : (
            <div className="fixture-host__preview-empty">
              <h2>No preview available</h2>
              <p>The first registered fixture will appear here.</p>
            </div>
          )}
        </main>
      </div>
    </div>
  )
}

function NotFound({ route }: { route: Extract<FixtureRoute, { kind: 'not-found' }> }) {
  return (
    <main className="fixture-host fixture-host__not-found">
      <p className="fixture-host__eyebrow">404</p>
      <h1>{route.reason}</h1>
      <p>
        No registered fixture or experiment matches <code>{route.pathname}</code>.
      </p>
      <a href="/experiments">Return to the fixture catalog</a>
    </main>
  )
}

function App() {
  const route = resolveFixtureRoute(window.location.pathname, fixtureRegistry)
  const pageTitle =
    route.kind === 'not-found'
      ? 'Fixture not found | KOKOCHI UI'
      : route.kind === 'catalog' && route.experimentId
        ? `${route.experimentId} | KOKOCHI UI fixtures`
        : 'Fixture catalog | KOKOCHI UI'

  useEffect(() => {
    document.title = pageTitle
  }, [pageTitle])

  if (route.kind === 'not-found') {
    return <NotFound route={route} />
  }

  if (route.kind === 'fixture') {
    return null
  }

  return <FixtureCatalog experimentId={route.experimentId} />
}

export default App
