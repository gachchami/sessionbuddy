# Wave 0 integration decisions

Status: accepted; local foundation gate complete
Date: 2026-08-08

This record resolves the choices raised by `architecture.md`, `data-architecture.md`, and `api-security.md`. Feature agents must treat these as shared contracts unless the primary integrator approves an ADR changing them.

## Accepted decisions

1. **Runtime:** Python 3.13 on Cloudflare Python Workers runs FastAPI/Pydantic, `/api/v1`, domain services, queue consumers, and Workflow exports. A React/Vite TypeScript frontend is compiled to same-origin static assets; there is no TypeScript or Node backend. Python Workers' beta status is accepted only subject to local and deployed-preview compatibility and performance gates.
2. **HTTP boundary:** all product reads and mutations use the same FastAPI services, authorization dependencies, and async scoped repositories. No frontend or auxiliary Worker may bypass them.
3. **Persistence:** one shared-schema D1 database per environment is authoritative. Preview, staging, and production use physically separate resources.
4. **Identifiers:** P0 uses Python's standard-library UUIDv4, which is available under Pyodide and requires no runtime dependency. UUIDv7 is deferred unless a compatible implementation and measured index-locality benefit justify it. IDs provide opacity, never authorization.
5. **Identity/profile:** `users` is authentication identity. A later organization-owned `people` record holds speaker PII/profile data. Linking requires verified ownership plus an explicit, audited workflow; email equality alone never grants access.
6. **Memberships:** every event member also receives an organization `member` relationship. Event administration is an explicit event grant. Organization admins receive organization-wide event permissions through policy without duplicated event rows.
7. **Unknown email:** requesting a magic link always returns the same response. Verification creates a user only when the challenge was issued from an approved public-submission or invitation context; otherwise it fails generically. The initial foundation implements the policy interface and a synthetic-development adapter, not open production provisioning.
8. **Sessions:** default to 12-hour idle and 30-day absolute expiry. Rotate after sign-in, privilege changes, and security events. Any concurrency overlap must be short and explicitly tested.
9. **Concurrency:** editable-resource version mismatches return `409 conflict` with the current opaque resource version when disclosure is authorized. The project does not mix this with `If-Match`/`412` semantics.
10. **Static/private assets:** reserve `STATIC_CONTENT` for generated frontend assets and `PRIVATE_ASSETS` for R2.
11. **Queues and real time:** the initial Python queue consumer ships in the same Worker bundle. Outbox dispatch and external delivery remain outside user-facing transactions. EventHub stays Python if its hibernatable WebSocket path passes the compatibility gate; otherwise a minimal TypeScript auxiliary Worker may own only transient invalidation fan-out.
12. **Uploads:** later portal work uses direct R2 presigned uploads through a narrow, measured SigV4 dependency. Uploads stay quarantined until completion validation and asynchronous scanning. Scanner choice and upload limits remain blocking decisions for the asset feature, not the foundation scaffold.
13. **Rate limiting:** feature code uses the injectable policy interface. Production thresholds are configuration selected after representative tests; D1 enforces correctness guarantees.
14. **OpenAPI:** generation and CI drift checks are mandatory. `/api/v1/openapi.json` is available in local and preview; production exposure defaults to authenticated organization administrators until an integration use case approves public exposure.
15. **Retention:** authentication challenges expire after 15 minutes; ordinary idempotency records after at least 24 hours; final submission keys through deadline plus 24 hours. Audit, outbox, user deletion, and asset retention require an approved policy before production and are not automatically purged meanwhile.
16. **Read replication:** foundation correctness paths use primary/session-consistent D1 reads. Replica use is deferred to measured, stale-tolerant read views.
17. **Source hosting:** GitHub or Forge is operational preference and does not affect the runtime architecture.
18. **Observability:** FastAPI middleware emits structured route-template metrics and safe `Server-Timing` breakdowns. The browser reports sampled Web Vitals/navigation timings. CI and staging retain machine-readable benchmark history keyed by dataset, commit, deployment, cold/warm state, and device profile; Section 8 regression gates block promotion.
19. **Operator consoles:** human-ready release, API, page, async, security, and storage/real-time dashboards are P0. Prefer managed/provider consoles backed by the shared telemetry contract; build only thin domain-specific operator views. Console access is least-privilege and audited.
20. **Future-feature gate:** every API, page, queue consumer, and Workflow must ship its instrumentation, SLO/benchmark scenario, redaction test, dashboard dimensions, and recovery/runbook coverage with the feature.

## Foundation scope boundary

Wave 0 scaffolds configuration, API conventions, health/readiness surfaces, schema migrations, authentication/RBAC interfaces, tenant-scoped persistence primitives, audit/outbox/idempotency primitives, deterministic foundation seeds, and test commands. It does not implement submission, speaker, evaluation, communication, agenda, or dashboard product journeys.

## Current verification status

- The Docker gate passes lint, 143 tests, local migrations, Workerd/Pyodide route checks, and a 200-request local health benchmark (0% errors, 8.7 ms p95 on the accepted 2026-08-08 run).
- The `development` Worker and its isolated D1 database are deployed and report their runtime environment from Cloudflare bindings.
- `GET /api/v1/foundation/database` performs a bounded `SELECT 1`, exposes only provider/environment/query duration, and records a `db` Server-Timing phase.
- The 2026-08-08 APAC client measurement of that route had zero errors but a 362 ms p95, above the provisional 250 ms SLO. Server-Timing decomposition attributed 227 ms p95 to the D1 binding call and approximately 135 ms to client/network overhead. Smart Placement is enabled for a measured before/after comparison; Wave 0 remains open until placement has enough traffic to decide and the result is either improved or assigned a justified regional SLO.
- The development console still requires a Cloudflare Access policy before it may expose non-synthetic operational or tenant data.
- Zero Trust Free activation was stopped at checkout because Cloudflare requires a payment card and explicit authorization for usage above the free allowance. No billing details or charge authorization were submitted by the implementation agent.
- The first post-placement request entered Cloudflare at MRS while D1 remained APAC. The initial 20-request result (242 ms D1 p95, 367 ms end-to-end p95) is a pre-decision baseline; the absence of a `cf-placement` response header indicates that Smart Placement had not yet selected a remote execution location.
- Signed-cookie verification, live-session validation, CSRF/origin/media-type guards, deterministic local rate limiting, and the cookie-to-actor-to-RBAC authentication pipeline are implemented and adversarially tested.
- The synthetic foundation console remains public by design and is constrained by an explicit response DTO and serialization tests. It must be protected before adding identity, tenant, incident, or other non-public operational data.
- The detailed local acceptance evidence and feature-wave handoff rules are recorded in `docs/wave-0-acceptance.md`.

## Deferred decisions with named gates

- Malware scanner and quarantine timeout: decide before speaker assets.
- Exact rate thresholds and WAF rules: decide before public preview load testing.
- Audit/export access and retention: decide before production data.
- Per-PR stateful resource provisioning mechanism: decide before enabling automatic remote previews.
- Real email domain and sender policy: decide before communication staging tests.
