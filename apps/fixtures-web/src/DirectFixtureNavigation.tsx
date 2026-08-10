import { experimentPath } from './fixture-route'

type DirectFixtureNavigationProps = {
  experimentId: string
}

export function DirectFixtureNavigation({
  experimentId,
}: DirectFixtureNavigationProps) {
  if (window.self !== window.top) {
    return null
  }

  return (
    <nav
      className="fixture-direct-navigation"
      aria-label="Direct fixture navigation"
    >
      <a href={experimentPath(experimentId)}>
        <span aria-hidden="true">←</span>
        Back to experiment overview
      </a>
    </nav>
  )
}
