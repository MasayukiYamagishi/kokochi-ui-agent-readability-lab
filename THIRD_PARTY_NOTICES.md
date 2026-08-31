# Third-Party Notices

Last reviewed: 2026-08-31

This repository includes or uses third-party software, font software, icons,
container images, and other materials.

These materials are excluded from the PolyForm Noncommercial License granted
by Masayuki Yamagishi. They remain subject to the licenses, notices, terms of
use, trademark policies, and brand guidelines of their respective owners.

This notice is provided for attribution and identification. It does not
replace or modify the authoritative terms published by each rights holder.
The inclusion of a third-party name, product, icon, or logo does not imply
affiliation with, sponsorship by, or endorsement from its owner.

## Runtime software dependencies

Exact versions and transitive dependencies are recorded in `pnpm-lock.yaml`
and `uv.lock`.

### JavaScript

| Package | License | Project |
| --- | --- | --- |
| `@fontsource/inter` | SIL Open Font License 1.1 | <https://fontsource.org/fonts/inter> |
| `react` / `react-dom` | MIT | <https://github.com/facebook/react> |

### Python

| Package | License | Project |
| --- | --- | --- |
| `httpx` | BSD-3-Clause | <https://github.com/encode/httpx> |
| `jsonschema` | MIT | <https://github.com/python-jsonschema/jsonschema> |
| `opentelemetry-api` | Apache-2.0 | <https://github.com/open-telemetry/opentelemetry-python> |
| `opentelemetry-exporter-otlp-proto-http` | Apache-2.0 | <https://github.com/open-telemetry/opentelemetry-python> |
| `opentelemetry-sdk` | Apache-2.0 | <https://github.com/open-telemetry/opentelemetry-python> |
| `opentelemetry-semantic-conventions` | Apache-2.0 | <https://github.com/open-telemetry/opentelemetry-python> |
| `pydantic` | MIT | <https://github.com/pydantic/pydantic> |
| `PyYAML` | MIT | <https://github.com/yaml/pyyaml> |
| `python-dotenv` | BSD-3-Clause | <https://github.com/theskumar/python-dotenv> |
| `typer` | MIT | <https://github.com/fastapi/typer> |

## Development and build dependencies

Development and build dependencies retain their upstream licenses. Direct
dependencies include:

| Packages | License |
| --- | --- |
| ESLint, its React plugins, `globals`, and `typescript-eslint` | MIT |
| `@types/node`, `@types/react`, and `@types/react-dom` | MIT |
| Vite and `@vitejs/plugin-react` | MIT |
| TypeScript | Apache-2.0 |
| mypy, pytest, pytest-cov, and Ruff | MIT |
| Playwright and pytest-playwright | Apache-2.0 |

The lockfiles are authoritative for the resolved package versions. Package
distributions and upstream repositories are authoritative for complete
license texts and attribution requirements, including transitive packages.

## Inter font

The fixture application uses Inter through `@fontsource/inter`.

- Copyright 2016 The Inter Project Authors
- License: SIL Open Font License 1.1
- Source: <https://github.com/rsms/inter>
- Local license copy: [Inter OFL 1.1](./licenses/fonts/Inter-OFL-1.1.txt)

## Vite and React starter assets

The following files originated from or are based on Vite React starter
materials:

- `apps/fixtures-web/public/favicon.svg`
- `apps/fixtures-web/src/assets/react.svg`
- `apps/fixtures-web/src/assets/vite.svg`

Vite and React source materials are distributed under the MIT License:

- Vite: <https://github.com/vitejs/vite/blob/main/LICENSE.md>
- React: <https://github.com/facebook/react/blob/main/LICENSE>

The Vite and React names and logos remain subject to their owners' applicable
trademark and brand policies.

## Observability and build container images

The development observability stack pulls third-party container images at
build or runtime. The images and all software bundled in them retain their
respective upstream licenses.

| Image | Project license / source |
| --- | --- |
| `grafana/otel-lgtm:0.27.1` | Apache-2.0 project; bundles Grafana, Loki, Prometheus, Pyroscope, Tempo, and OpenTelemetry components with their own terms. <https://github.com/grafana/docker-otel-lgtm> |
| `otel/opentelemetry-collector:0.156.0` | Apache-2.0. <https://github.com/open-telemetry/opentelemetry-collector> |
| `golang:1.25.0-alpine3.22` | Docker Official Image packaging under BSD-3-Clause; included Go and Alpine packages retain their own licenses. <https://github.com/docker-library/golang> |

Consult the image metadata and package inventories for complete notices before
redistributing a container image.

## Rights not granted

Nothing in this file grants permission to use a third party's trademarks,
service marks, logos, trade dress, product names, copyrighted brand artwork,
or rights of publicity or endorsement.

Users of this repository are responsible for reviewing the current
authoritative terms before reusing third-party material.
