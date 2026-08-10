import '@fontsource/inter/latin-400.css'
import '@fontsource/inter/latin-700.css'
import { createRoot } from 'react-dom/client'
import './fixture-direct.css'
import { DirectFixtureNavigation } from './DirectFixtureNavigation'
import { fixtureRegistry } from './fixture-registry'
import { resolveFixtureRoute } from './fixture-route'

const rootElement = document.getElementById('root')
if (!rootElement) {
  throw new Error('Fixture host root element is missing')
}

const root = createRoot(rootElement)
const route = resolveFixtureRoute(window.location.pathname, fixtureRegistry)

if (route.kind === 'fixture') {
  document.title = `${route.fixture.fixtureId} | ${route.fixture.experimentId}`
  route.fixture
    .load()
    .then(({ default: FixtureComponent }) => {
      root.render(
        <>
          <DirectFixtureNavigation
            experimentId={route.fixture.experimentId}
          />
          <FixtureComponent />
        </>,
      )
    })
    .catch((error: unknown) => {
      console.error(error)
      root.render(
        <>
          <DirectFixtureNavigation
            experimentId={route.fixture.experimentId}
          />
          <main role="alert">
            <h1>Fixture failed to load</h1>
            <p>{route.fixture.route}</p>
          </main>
        </>,
      )
    })
} else {
  import('./App').then(({ default: App }) => {
    root.render(<App />)
  })
}
