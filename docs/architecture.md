# Sessionbuddy application architecture

Status: platform foundation decision record
Last verified: 2026-08-08  
Scope: runtime, deployment, module, and operational architecture. Domain schema, API semantics, and authorization policy are specified separately.

## 1. Decision summary

Sessionbuddy uses a Python backend on Cloudflare and a deliberately small browser application:

- Python 3.13 Cloudflare Workers, FastAPI, Pydantic, and `pywrangler` implement the application and `/api/v1`.
- A React/Vite TypeScript single-page frontend provides the form builder, agenda editor, dashboards, and portals. It is compiled to static assets and served same-origin by the Python Worker.
- Public entry HTML is a minimal cacheable shell with route-specific preload data where safe. React route chunks hydrate only the requested experience; large admin modules are lazy-loaded.
- D1 is the transactional source of truth; R2 stores private assets; Queues execute asynchronous effects; Workflows coordinate durable timers and multi-step communication; an event-scoped Durable Object fans out dashboard invalidations.
- Resend is reached only from a queue consumer. No user-facing request waits for email, calendar generation, scanning, export, or integration delivery.

This is a modular monolith, not a collection of feature services. Start with one Python Worker bundle exporting HTTP, queue, Workflow, and Durable Object entry points where supported. Split background consumers only when measurements or deployment constraints justify the added boundary.

Python Workers are still in beta and require the `python_workers` compatibility flag. They execute CPython through Pyodide WebAssembly inside V8, not a conventional Linux CPython process. The architecture therefore has a mandatory compatibility and performance exit gate before feature development; see Section 3. Cloudflare documents FastAPI, Pydantic, D1, R2, Queues, Workflows, and Durable Objects as supported through Python Workers and their FFI ([Python Workers](https://developers.cloudflare.com/workers/languages/python/), [FastAPI](https://developers.cloudflare.com/workers/languages/python/packages/fastapi/)).

## 2. Selected stack

| Concern | Choice | Reason |
| --- | --- | --- |
| Backend/runtime | Python 3.13 on Cloudflare Python Workers | User-selected language; edge deployment and direct Cloudflare bindings remain available. |
| HTTP/API | FastAPI with the Workers ASGI adapter | Typed async handlers, Pydantic validation, and generated OpenAPI using an officially documented Workers path. |
| Validation/settings | Pydantic | One explicit validation layer for request, response, job-envelope, and non-secret configuration models. |
| Packaging/deploy | `uv`, `workers-py`, `workers-runtime-sdk`, `pywrangler` | Cloudflare's current supported Python packaging, local-development, and deployment workflow. |
| Browser frontend | React + Vite + TypeScript | Complex form-building, drag/drop, and real-time screens benefit from React; Vite enables route chunks and small static output without putting Node in production. |
| Persistence | Explicit SQL migrations and narrow D1 repository adapters | Reviewable SQL and direct control of hot queries; do not assume a CPython database driver works in Pyodide. |
| Testing | pytest for pure/domain tests, Workers-compatible integration tests, Playwright/axe, and k6-compatible load scripts | Separates fast Python logic from actual Pyodide/binding verification and browser/performance gates. |
| JavaScript packages | npm with committed lockfile | Deterministic frontend and Wrangler tooling; do not mix JS package managers. |

FastAPI serves `/api/v1`, `/health`, authorized WebSocket upgrade/proxy routes, and the compiled browser shell/assets. API handlers are `async` and call Workers bindings through narrow adapters. FastAPI's OpenAPI output is checked into `openapi/openapi.json` and regenerated in CI. Production must not run Uvicorn, Gunicorn, a Node server, or a container.

The frontend calls the same-origin versioned API. It does not hold domain truth, derive permissions, or access bindings. TanStack Query may be introduced only if native route loaders and a small request cache become insufficient; a broad global state library is not a baseline dependency.

## 3. Python Workers beta and compatibility gate

Cloudflare Python Workers use Pyodide, a CPython-to-WebAssembly port. Cloudflare currently labels the runtime beta. The standard library is broad but excludes or limits OS/process facilities including functional `threading`, `multiprocessing`, `fcntl`, and persistent filesystem access. The filesystem is ephemeral and isolate-local ([Python standard library](https://developers.cloudflare.com/workers/languages/python/stdlib/), [runtime model](https://developers.cloudflare.com/workers/languages/python/how-python-workers-work/)).

Package rules:

- Runtime dependencies must be pure Python, have a PyEmscripten wheel, or be included by Pyodide. A normal manylinux/macOS wheel is not evidence of compatibility.
- Only asynchronous HTTP clients are supported; use `httpx` or `aiohttp` if direct outbound HTTP is unavoidable. Prefer the Workers `fetch` interface behind an adapter.
- Do not introduce packages that require sockets, native daemons, threads, process pools, local database files, OS keychains, or writable persistent disk.
- Keep top-level imports and dependency count small. Cloudflare snapshots initialized Pyodide memory at deploy time to reduce cold start, but bundle/import size still requires measurement.
- Pin Python to `>=3.13,<3.14`, commit `uv.lock`, and pin a reviewed `compatibility_date`. Runtime and dependency upgrades are explicit changes with performance and contract checks.

Before product feature agents branch, an isolated preview must prove all of the following:

1. `uv sync`, `uv run pywrangler dev`, build/deploy, and an empty-storage migration work reproducibly.
2. FastAPI request/response validation and generated OpenAPI work in the actual Python Worker runtime.
3. Session signing, cryptographic random-token creation, hashing, CSRF comparison, and time-zone behavior use supported APIs and meet security tests.
4. D1 transactions, R2 operations/signing design, Queue producer/consumer, Workflow execution, and Durable Object/WebSocket access work from Python through the supported SDK/FFI.
5. Every locked runtime dependency imports and exercises its critical path under local `workerd` and deployed preview; a CPython-only pytest pass is insufficient.
6. A representative public read, authenticated read, D1 write/queued-communication transaction, and list query meet the requirements p95 budget with production-shaped data.
7. Cold-isolate latency, CPU time, Worker bundle size, and memory are recorded. Any miss blocks the Python architecture pending dependency reduction or explicit runtime reconsideration.

This gate is a risk-control decision, not permission to silently replace Python. If Python Workers beta lacks a required binding or cannot meet the release budgets, the integrator must present measured evidence and obtain a new architecture decision.

## 4. Runtime topology

```text
Browser / approved API client
        |
Cloudflare edge: TLS, WAF/rate rules, public-version cache
        |
Python application Worker (Pyodide)
  |-- static React/Vite shell and fingerprinted assets
  |-- FastAPI /api/v1 through Workers ASGI adapter
  |-- D1 transaction + durable communication writer
  |-- R2 signing/authorized-download adapter
  |-- Queue producer/consumer entry point
  |-- Workflow entry classes
  `-- EventHub Durable Object RPC/WebSocket proxy
        |
        |-- D1: authoritative domain and communication delivery state
        |-- R2: private uploads
        |-- Queue: email, calendar, scan and projection jobs
        |-- Workflows: reminders and durable communication
        `-- EventHub DO per event: transient invalidation fan-out
```

The Durable Object is not authoritative product storage. A client connects to the object derived from an opaque event ID, receives small version/invalidation envelopes, and reloads an authorized D1 snapshot after reconnect or a gap. Use hibernatable WebSockets, batch bursts, and never create one global singleton. If Python WebSocket hibernation or Durable Object ergonomics fail the compatibility gate, a minimal TypeScript auxiliary Worker may own only EventHub; all domain/API logic remains Python and the boundary is a typed service binding.

## 5. Module boundaries

```text
backend/
  entry.py                       Worker entry point and composition only
  app.py                         FastAPI construction and middleware
  api/                           /api/v1 routers, dependencies, OpenAPI
  features/
    events/
    submissions/
    speakers/
    evaluations/
    communications/
    agenda/
    dashboard/
  platform/
    auth/                        identity and session primitives
    authorization/               centralized permissions and tenant scope
    db/                          D1 adapters and transactions
    storage/                     R2 keys, signing, quarantine
    messaging/                   queue envelopes, producer, consumers
    workflows/                   Workflow entry classes and adapters
    realtime/                    EventHub DO/proxy and protocol
    observability/               logs, metrics, timing and redaction
    config/                      typed binding/settings access
  shared/                        IDs, clocks, errors and pagination
frontend/
  src/routes/                    route-level browser entry points
  src/features/                  UI organized by the same product slices
  src/components/                accessible shared primitives
  src/api/                       generated client types and fetch adapter
migrations/                      ordered D1 SQL; one migration owner
openapi/                         generated checked contract
tests/                           integration, contract, security, E2E, load
```

Rules:

1. API routers translate HTTP concerns and call feature application services; they do not issue D1 statements or access bindings directly.
2. Features own use cases and domain rules. They depend on narrow platform protocols and `shared`, not another feature's persistence implementation.
3. Cross-feature side effects use durable capability-owned records and versioned queue envelopes or an explicit application-service protocol.
4. Platform modules contain mechanisms. Central authorization accepts named permissions and resource context; handlers cannot bypass it.
5. `entry.py` and `app.py` are composition roots and remain small. Feature registration is through feature-owned routers assembled in one registry.
6. The frontend uses the generated API types, but generated code is not imported into domain modules.
7. Every queue/WebSocket envelope has a name, integer version, opaque tenant/event identifiers, correlation ID, idempotency key, and size limit.

## 6. Binding contract

Use stable binding names across environments; only resource identifiers change.

| Binding | Name | Responsibility | Request-path rule |
| --- | --- | --- | --- |
| D1 | `DB` | Domain records, sessions, idempotency, audit, communication delivery | Bounded, indexed, parameterized queries only. |
| R2 | `PRIVATE_ASSETS` | Headshots, slides, documents under opaque tenant keys | Metadata/signing only; upload bytes go directly to R2. |
| Static assets | `STATIC_CONTENT` | Built React shell and fingerprinted browser assets | Immutable caching for hashes; controlled shell caching. |
| Queue | `ASYNC_JOBS` | Delivery, scan, calendar, projection, integration jobs | Publish after durable state commits; never await providers. |
| Workflow | `COMMUNICATION_WORKFLOW` | Reminders, cancellation/reschedule, durable multi-step sends | Start or signal only after committed intent. |
| Durable Object | `EVENT_HUB` | Per-event dashboard invalidation fan-out | Failure never changes the domain mutation outcome. |
| Analytics Engine, if selected | `METRICS` | Low-cardinality latency and queue-delay metrics | Never include PII, answers, tokens, or object URLs. |

D1 access stays behind an async repository adapter because Python reaches Workers bindings through the SDK/FFI, not a DB-API SQLite driver. Convert JS/Python values in one platform module and return plain typed domain records.

R2 presigned URLs use its S3-compatible API. Signing credentials are Worker secrets; URLs are short-lived and method/key/content constrained where possible. Bucket CORS is explicit. If the selected signer cannot constrain size/type cryptographically under Pyodide, upload completion validation and quarantine are mandatory before read access.

Queue consumers assume at-least-once delivery: use deterministic job keys, persist attempts, make provider operations idempotent where possible, acknowledge after the terminal state is stored, and configure retries/backoff/dead-letter handling. Workflows own long sleeps and durable orchestration, not routine queue retry. A deterministic workflow instance re-reads current due date/status before sending.

## 7. Configuration and local workflow

Commit `wrangler.jsonc`, `pyproject.toml`, `uv.lock`, `package.json`, and `package-lock.json`. Generate binding types or maintain checked protocols from the current Python SDK; validate binding presence during startup. Store secrets through Wrangler/Cloudflare secret management and `.dev.vars`; commit only `.dev.vars.example`.

```text
wrangler.jsonc                 bindings, compatibility date/flag, environments
pyproject.toml + uv.lock       Python runtime and dev dependencies
package.json + package-lock    frontend/Vite/Wrangler dependencies
.dev.vars.example              names and harmless local defaults
.dev.vars                      ignored local secrets
migrations/                    D1 migrations
.local/                        ignored, disposable local binding state
```

Canonical local loop:

```bash
uv sync --frozen
npm ci
npm run frontend:build
uv run pywrangler d1 migrations apply DB --local
uv run pywrangler dev
```

Required scripts should expose `dev`, `frontend:dev`, `build`, `preview`, `db:reset`, `db:migrate`, `db:seed`, `openapi:check`, `test:unit`, `test:workers`, `test:e2e`, `test:a11y`, `test:load`, and `test:perf`. `dev` must build/watch the frontend and run the Python Worker with local bindings. `db:reset` creates or replaces only a validated repository-local disposable state path.

Pure domain tests run on standard CPython for speed, but binding/API tests run through `pywrangler dev` or the supported Workers test mechanism. Remote bindings are opt-in only against dedicated development resources. Never point a developer loop at production. Workflows and other platform-specific behavior require deployed staging verification even when locally emulated.

## 8. Environments and deployment

| Environment | Purpose | Data/effects |
| --- | --- | --- |
| Local | Fast development and deterministic tests | Simulated bindings, synthetic seed, captured email, fake provider adapters. |
| PR preview | Deployed Pyodide/package/binding acceptance | Per-PR or allocated non-production resources, synthetic data, test-recipient allow-list. |
| Staging | Production-platform release rehearsal | Persistent staging resources, real bindings, test email domain, production-shaped seed. |
| Production | User traffic | Dedicated bindings/secrets and progressive deployment with rollback. |

Define explicit `preview`, `staging`, and `production` Wrangler environments with different Worker names and physically separate D1 databases, R2 buckets, Queues, Workflow configuration, Durable Object namespaces where required, secrets, and analytics datasets. A versioned Preview URL isolates code, not storage; CI must provision/allocate, migrate, seed, and clean preview resources.

Pipeline:

1. Frozen installs; Ruff formatting/lint, Pyright type checking, frontend lint/type check, dependency/secret scanning, OpenAPI generation, and binding/config checks.
2. Pure unit tests plus deployed-runtime package import smoke, Worker integration, authorization, migration-from-empty, E2E, accessibility, and query-plan checks.
3. Build frontend once, package Python Worker, and record Python/JS dependency sizes, Worker bundle/snapshot metrics, and browser chunk sizes.
4. Deploy an isolated preview and run the Section 3 compatibility gate plus contract/journey smoke tests.
5. On main, apply forward-compatible migrations to staging, deploy the same commit, exercise real bindings, and run the large-seed load/performance comparison.
6. Create a production version, migrate safely, shift traffic progressively, and monitor errors, tail latency, D1 failures, Python exceptions, CPU, memory, and queue age. Roll back code when thresholds breach; roll forward schema where required.

## 9. Performance architecture and release gate

Requirements Section 8 blocks release. The Python choice does not relax any budget.

- Keep FastAPI middleware, Pydantic models, imports, and dependencies minimal; measure model validation and serialization on large responses.
- Serve fingerprinted browser assets immutably at the edge. Cache only explicitly public, version-addressed data; authenticated HTML/JSON is `private, no-store` unless reviewed otherwise.
- Keep application and API same-origin to avoid CORS latency and simplify secure cookies.
- The shell contains useful route-specific loading/identity context but no private cacheable data. Lazy-load form-builder, agenda, rich-editor, chart, and admin-only chunks.
- Perform independent D1 reads concurrently only when that reduces latency without creating excessive statements. Every list uses cursor pagination and tenant-leading indexes verified by `EXPLAIN QUERY PLAN` on the 10k seed.
- Keep large answers, message bodies, audit detail, and asset metadata out of lists; select named columns.
- Commit domain state and durable communication intent together and return after commit. External work is asynchronous.
- Use direct R2 upload, explicit responsive image dimensions, and lazy loading.
- Emit `Server-Timing` for total Worker, auth, D1, validation, and serialization duration without private data.
- Report warm and cold p50/p75/p95, error rate, CPU, memory, D1 time/query count, queue delay, browser bytes, LCP, INP, and CLS.
- Run a fixed benchmark manifest against the same named seed, routes, viewport/device profiles, and concurrency before comparing builds. Store raw samples plus a machine-readable summary keyed by Git SHA, Worker deployment/version, environment, compatibility date, dependency-lock hashes, seed version, and timestamp.
- A missed p95, failed Core Web Vital, or greater-than-10% staging regression blocks promotion. Beta-runtime waivers require an owner, reason, expiry, and rollback plan.

## 10. Observability and operations

Observability is part of the Foundation Gate, not post-MVP hardening. Before feature work, one instrumented API route and one instrumented browser route must demonstrate local capture, preview/staging ingestion, safe redaction, benchmark comparison, and an actionable regression failure.

Emit one structured request-completion event at every API boundary. It includes timestamp, environment, Git SHA, Worker deployment/version, compatibility date, request/correlation ID, route template (never raw path), method, status class, cache state, response bytes, and durations for total request, authentication, authorization, D1 total/query count, validation, domain service, and serialization. Nested spans may add queue/Workflow/provider timing, but they share the correlation ID. `Server-Timing` exposes the safe request/authz/DB/validation/serialization breakdown to tests and browser tooling; it must not reveal record IDs, policy reasons, SQL, or tenant data.

Emit structured JSON at queue, Workflow, and Durable Object boundaries with the same environment/version dimensions plus job ID, result class, duration, Python exception class, queue delay, and retry count. Never log email addresses, cookies, magic-link material, form answers, message bodies, signed URLs, filenames, authorization headers, raw query strings, or high-cardinality user/resource identifiers. Redaction is centralized and covered by tests that inject representative secrets and private fields.

The browser reports Navigation Timing and Web Vitals (LCP, INP, CLS and TTFB), route template, navigation type, viewport/device class, effective connection class when available, frontend build hash, and Worker deployment/version returned by a response header. It never sends identity, full URL, query text, form data, or DOM content. Sample normal production traffic, but retain all errors and budget violations within privacy/rate limits.

Sink strategy:

- Local: human-readable console plus newline-delimited structured events and benchmark artifacts in a validated repository-local ignored `.local/observability/` path.
- PR/CI: upload API benchmark JSON, browser trace/Har, Web Vitals summary, bundle report, and comparison result as immutable CI artifacts; post a concise baseline delta to the change review.
- Staging: Workers Logs/Trace Events plus Analytics Engine or the approved metrics backend; run the fixed production-shaped benchmark and compare it with the latest accepted main baseline.
- Production: sampled structured logs/traces plus low-cardinality SLO metrics and sampled real-user measurements. Dashboards group by route template and deployment/version so a rollout can be compared and rolled back quickly.

Keep accepted benchmark summaries for the project lifetime and raw CI artifacts for at least 90 days, subject to the final retention policy. Maintain an append-only manifest/index that identifies the accepted baseline for each route/journey; never overwrite a baseline in place. Hardware/network-sensitive local measurements are diagnostic, while staging is the promotion baseline.

CI fails when the benchmark manifest is incomplete, instrumentation dimensions disappear, a private-data redaction test fails, any absolute Section 8 budget is missed, or staging p75/p95 regresses by more than 10% from the accepted baseline. Bundle-size and query-count ceilings fail independently even if wall time happens to pass. Alert on route 5xx/p95, Python startup/import failures, CPU/memory growth, D1 errors/slow queries, queue age/dead letters, Workflow failures/lateness, auth/rate anomalies, real-time propagation lag, and provider/R2 mismatches. Runbooks cover benchmark-baseline promotion, false-regression investigation, queue replay, dead letters, stuck Workflow recovery, session revocation, R2 quarantine, D1 restore, and code rollback.

### 10.1 Human operational surface

Prefer provider-managed dashboards over building a second observability product. Version-control dashboard-as-code definitions where supported and add a thin Sessionbuddy operator console only for domain actions that managed tooling cannot express safely, such as replaying a dead-lettered communication or inspecting a redacted event onboarding projection.

The minimum console set is release health, API explorer, page performance, async operations, security/access, and storage/real-time health as defined in requirements Section 8.5. Each console must present deployment/version comparison, sample count and sampling state, link aggregates to redacted correlated traces, distinguish missing data from zero, and include the relevant runbook. Console access uses a separate operator permission boundary, is audited, and never follows ordinary event-admin access automatically.

### 10.2 Standard diagnostic path

The debugging path is browser navigation/route transition -> correlated API request -> authentication/authorization -> D1 statements -> durable communication/Queue/Workflow -> provider callback. The shared observability package owns correlation propagation, field names, phase timers, redaction, cardinality checks, sampling, and exporters. A feature supplies only a stable owner, route/job/page template, applicable low-cardinality outcome, SLO, and benchmark journey.

For a regression, identify the deployment and symptom, reproduce with synthetic data, split browser/network/Worker/D1 time, compare like-for-like baselines, inspect query plan/count/rows and response/bundle size, add a regression test, verify in staging, and record before/after evidence. Ad hoc production body logging and unaudited debug endpoints are prohibited.

### 10.3 Feature registration contract

Feature registration fails review/CI when a new API route, page template, queue handler, or Workflow lacks its observability manifest entry. The manifest drives benchmark discovery, dashboard grouping, alert ownership, and runbook links. Automated tests assert that runtime route/job/page registries and the manifest remain synchronized, preventing future agents from silently shipping uninstrumented paths.

## 11. Explicit non-goals

- No Node backend, Hono, React Router server runtime, Uvicorn/Gunicorn, container, Kubernetes, or regional Python server.
- No native Python/OS-dependent package without a verified PyEmscripten/Pyodide path.
- No synchronous client, threads, multiprocessing, persistent local filesystem, or SQLite driver assumptions.
- No microservices or separate GraphQL layer.
- No Airtable access in a user request; a P1 mirror consumes asynchronous events.
- No KV as authoritative domain, session, idempotency, authorization, or conflict state.
- No Durable Object as product source of truth or global singleton.
- No synchronous email, scanning, calendar generation, export, webhook, or integration call.
- No service worker/offline shell in P0 and no broad client global-state framework by default.
- No production data/secrets in local or preview, and no automatic production migration from a PR.
- No P1/P2 feature may add work to P0 paths before a performance design is approved.

## 12. Integrator reconciliation before scaffolding

1. Record Python Workers beta as an accepted project risk subject to the Section 3 exit gate; this replaces the earlier React Router/Hono backend recommendation.
2. Confirm plain React/Vite TypeScript for the browser. It retains complex interaction quality while keeping all server/domain/API code Python.
3. Prove Python entry-point support for Queue, Workflow, and Durable Object exports in one bundle. Default to one Worker; allow a minimal TypeScript EventHub auxiliary Worker only if Python hibernatable WebSockets fail the gate.
4. Reserve `STATIC_CONTENT`, `PRIVATE_ASSETS`, `DB`, `ASYNC_JOBS`, `COMMUNICATION_WORKFLOW`, and `EVENT_HUB` before generated code lands.
5. Select the R2 signing and malware-scanning implementations only after upload limits are fixed and package compatibility is proven.
6. Decide PR resource automation; Preview URLs do not isolate D1/R2/Queue state.
7. Pin exact `workers-py`, `workers-runtime-sdk`, `pywrangler`, FastAPI, Pydantic, Python, Node, and frontend versions during scaffold rather than copying floating versions from examples.
