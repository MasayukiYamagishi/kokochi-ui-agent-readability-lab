# Security Policy

## Supported Versions

| Target | Supported |
| --- | --- |
| The latest `main` branch | Yes |
| Older commits, forks, and modified copies | No |

## System and Scope

KOKOCHI UI Agent Readability Lab is a local experiment framework for
evaluating how LLMs interpret Web UI structures.

The repository contains:

- a React and Vite fixture application
- a Python experiment runner, evaluator, and result store
- experiment configuration, prompts, and ground truth
- Playwright-based browser capture and tests
- an OpenTelemetry Collector and Grafana LGTM development stack
- scripts and repository validation tools

The project does not intentionally provide a public hosted service, user
accounts, authentication, payments, or a multi-tenant authorization boundary.

The experiment runner can connect to operator-configured Ollama and OTLP
HTTP(S) endpoints, launch a local browser, process model responses, and persist
experimental inputs and results.

This policy covers:

- source code and configuration in this repository
- the local fixture application and experiment runner
- experiment input validation and result storage
- telemetry instrumentation and export
- browser automation initiated by the project
- Docker and observability configuration
- repository configuration and automation that affect source integrity

## Threat Model and Trust Boundaries

The local operator and reviewed repository configuration are trusted.

The following inputs must be treated as untrusted until validated:

- LLM responses and provider HTTP responses
- browser-visible fixture and external web content
- imported or modified experiment definitions
- dependency updates and pull request content
- data received from configured external services

Operator-configured Ollama and telemetry endpoints may refer to another machine
on the local network. Their operators and transport security are outside this
repository's control.

Important assets include:

- the integrity and reproducibility of experiment definitions and results
- approved ground truth and recorded source provenance
- prompts, model inputs, raw responses, and captured browser data
- credentials and other secrets present in the local environment
- the operator's local filesystem and network access
- the integrity of the repository and its default branch

GitHub, package registries, browsers, Ollama, container registries, and
observability products are third-party trust boundaries.

## Security Properties

The following properties must hold:

- Repository identifiers and artifact paths must be validated before use and
  must not escape their intended experiment or result directories.
- Experiment configuration and ground truth must reject malformed,
  non-canonical, duplicate, or unexpected data.
- Approved ground truth must remain bound to its reviewed content hash.
- LLM responses, provider responses, and browser content must be handled as
  data and must not be evaluated as code or interpolated into shell commands.
- Secrets, credentials, private keys, and credential-like configuration fields
  must not be persisted in result manifests or exported as telemetry.
- Prompt text, generated content, and raw request or response bodies must not
  be added to telemetry unless an explicit, documented opt-in requires it.
- Provider and telemetry endpoint URLs must not contain embedded credentials.
- Connection configuration must remain separate from reproducible experiment
  result metadata.
- Local fixture and observability services must bind to loopback interfaces by
  default unless the operator explicitly changes the configuration.
- Tests that contact the public network must remain explicitly opt-in.
- Partial or interrupted result writes must not be published as complete runs.
- Repository automation must use the minimum required permissions and must not
  expose secrets to untrusted pull request code.

## Reporting a Vulnerability

Do not report security vulnerabilities through a public GitHub issue,
discussion, pull request, or social media post.

Use GitHub's private vulnerability reporting form:

<https://github.com/MasayukiYamagishi/kokochi-ui-agent-readability-lab/security/advisories/new>

Please include:

- a concise description of the issue
- the affected file, command, workflow, or commit
- reproducible steps
- the expected and actual behavior
- the realistic security impact
- a minimal proof of concept when needed
- any known mitigations

Do not include active credentials, secrets, or unrelated personal information.
Redact sensitive values whenever possible.

Reports may be submitted in Japanese or English.

## Reportable Security Issues

Examples of reportable issues include:

- path traversal or arbitrary file access outside an experiment or result root
- unsafe file writes, overwrites, or partial-result publication
- command, code, or template injection through model or browser-controlled data
- unintended disclosure of prompts, raw responses, credentials, or private
  experimental data
- an attacker-controlled input that can redirect network requests to an
  unintended endpoint
- unintended script execution in the fixture application
- bypass of ground-truth approval, content hashes, or source provenance
- a dependency or repository automation issue that can compromise the
  repository, experiment results, or maintainer credentials
- bypass of branch protections or unauthorized modification of `main`

## Out of Scope

The following are generally out of scope unless a concrete impact on this
repository, the operator, or protected experimental data is demonstrated:

- model quality, hallucinations, scoring disagreements, or nondeterminism
- prompt injection that only changes the model output being evaluated and does
  not cause code execution, secret exposure, or unintended network access
- behavior of an endpoint intentionally configured by a trusted local operator
- local development server findings that require prior control of the
  operator's machine and do not cross another security boundary
- vulnerabilities affecting only old commits, forks, or modified copies
- dependency advisories that are not reachable or exploitable in this project
- automated scanner output without manual validation
- vulnerabilities in GitHub, Ollama, browsers, package registries, container
  images, or other third-party products
- denial-of-service or high-volume testing that could disrupt a local or
  third-party service

Report vulnerabilities in third-party products directly to their maintainers.

## Response Process

The maintenance targets for a valid report are:

- acknowledgment within 7 calendar days
- an initial assessment within 14 calendar days
- periodic updates when investigation or remediation takes longer

These are targets rather than guaranteed service-level commitments.

Fix and disclosure timing depends on severity, exploitability, affected users,
and third-party coordination. Please allow a reasonable remediation period
before public disclosure.

This project does not currently operate a paid bug bounty program.

## Known Limitations and Compensating Controls

The experiment runner, Playwright browser, Ollama, Docker, and observability
stack execute with permissions available to the local operator. This repository
does not provide an operating-system sandbox for those processes.

Raw experimental inputs and outputs may contain sensitive information and are
stored on the local filesystem without repository-provided encryption.
Operators are responsible for filesystem permissions, retention, backups, and
sharing controls.

Ollama and telemetry endpoints may run on another machine. Operators are
responsible for network access controls and transport security when those
endpoints are not restricted to loopback.

Schema validation, content hashes, write-once result boundaries, loopback
bindings, explicit external-test opt-in, and secret-field rejection reduce
risk but do not make third-party services or untrusted models inherently safe.
