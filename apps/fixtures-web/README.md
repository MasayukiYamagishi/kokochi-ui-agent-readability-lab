# Fixture host

This Vite application lists experiment fixtures and gives each fixture a stable,
direct URL. The catalog renders previews in iframes so catalog navigation and
styles do not enter the fixture DOM or measurement target.

The top-level direct URL includes a back link outside `[data-fixture-root]`.
Iframe previews omit that link, and capture code must scope measurements to the
fixture root so host navigation never enters an experiment input.

No routing dependency is used. The host has a fixed, small route grammar, and the
local matcher keeps the registry identifiers and URLs explicit.

## Add a fixture

For an experiment named `<experiment-id>`, keep its canonical files together:

```text
experiments/<experiment-id>/
├─ README.md
├─ config.yaml
├─ fixture-manifest.json
├─ fixtures/
│  ├─ <fixture-id>.tsx
│  ├─ shared.css
│  └─ shared/
│     └─ <shared-component>.tsx
└─ tests/
   └─ fixture.spec.ts
```

The fixture module must default-export its React component. Its single fixture
root must expose the exact registry identifiers and version:

```tsx
export default function ExplicitLabel() {
  return (
    <main
      data-fixture-root
      data-experiment-id="form-group-membership-reconstruction"
      data-fixture-id="explicit-label"
      data-fixture-version="v1"
    >
      {/* Experiment-owned UI */}
    </main>
  )
}
```

Keep experiment-shared styling in
`experiments/<experiment-id>/fixtures/shared.css`. Keep the fixture contract test
in `experiments/<experiment-id>/tests/fixture.spec.ts`. The fixture host test
command discovers every contract test at that canonical path automatically.

Declare the catalog metadata in the experiment-owned `fixture-manifest.json`:

```json
{
  "experimentTitle": "Form label association",
  "experimentDescription": "Compare semantic label associations.",
  "fixtures": [
    {
      "fixtureId": "explicit-label",
      "fixtureVersion": "v1",
      "description": "Inputs use explicit label associations."
    }
  ]
}
```

`src/fixture-registry.ts` discovers manifests automatically, derives the
experiment identifier from the manifest directory, and derives each canonical
source path from its fixture identifier. Adding an experiment or fixture does not
require editing the central registry. The registry resolves the derived path
through Vite's lazy module map, so the manifest and loaded module cannot diverge.
It also validates kebab-case identifiers and rejects duplicate routes or
conflicting experiment metadata. The derived source path is:

```text
../../../experiments/<experiment-id>/fixtures/<fixture-id>.tsx
```

The registration produces these URLs without aliases:

- catalog: `/experiments`
- experiment overview: `/experiments/<experiment-id>`
- fixture: `/experiments/<experiment-id>/<fixture-id>`

## Inspect locally

External deployment is not required for fixture development or experiment runs.
From the repository root, start Vite for hot reload and quick visual checks:

```bash
pnpm dev
```

Open `http://localhost:5173/experiments`, select a fixture in the left navigation,
and inspect the isolated preview in the main area. Then follow **Open direct URL**
to verify the fixture without catalog DOM or CSS.

To inspect the optimized production build locally, use two terminals:

```bash
# Terminal 1
pnpm build
pnpm preview

# Terminal 2 (or a browser)
# Open http://localhost:4173/experiments
```

Registered experiment fixtures use the same registry and stable URLs in both
development and the local production preview. `dist/404.html` is emitted for an
optional future static host, but no external hosting service is required by this
workflow.

Before submitting a change, run:

```bash
pnpm --filter fixtures-web lint
pnpm --filter fixtures-web test
pnpm typecheck
pnpm build
uv run pytest -v tests/browser
```

The host-contract fixtures are visible in normal development so catalog selection
and preview behavior can be checked before the first experiment fixture exists.
They are also included by the `fixture-test` build mode for browser regressions,
but are not experiment fixtures and are excluded from normal production builds.

