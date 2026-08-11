# Sessionbuddy API, authentication, and authorization foundation

Status: accepted platform foundation architecture decision
Scope: the shared foundation required by `requirements.md` Section 4; no product-feature API is specified here.

## 1. Decisions and constraints

Sessionbuddy will expose one JSON HTTP API from a Cloudflare Python Worker using **FastAPI** and **Pydantic v2**. Pydantic request/response models are the implementation-time validation source and FastAPI generates the OpenAPI document from the same route registry. Cloudflare provides an ASGI adapter and explicitly supports [FastAPI on Python Workers](https://developers.cloudflare.com/workers/languages/python/packages/fastapi/).

Use Python's `secrets`, `hashlib`, and `hmac` modules for random tokens, hashing, signing, and constant-time comparison; access D1, R2, Queues, Workflows, Secrets, and the Workers Rate Limiting binding asynchronously through the Workers SDK/FFI behind Python protocols. D1 prepared statements are required for variable SQL. Do not use blocking I/O, threads, processes, sockets, or packages that assume a conventional OS.

Python Workers execute CPython semantics through Pyodide/WebAssembly and remain a beta platform. The runtime has an ephemeral filesystem, no functional `threading` or `multiprocessing`, and supports only pure-Python, PyEmscripten, or Pyodide-provided packages; outbound HTTP clients must be asynchronous. These constraints are documented in Cloudflare's [package support](https://developers.cloudflare.com/workers/languages/python/packages/) and [standard-library limitations](https://developers.cloudflare.com/workers/languages/python/stdlib/). Every production dependency must therefore pass an import/startup smoke test under `uv run pywrangler dev` and an isolated preview deployment before adoption. Pin the compatibility date and `python_workers` flag, and upgrade them deliberately with the full gate.

Identifiers exposed outside the database use the approved P0 UUIDv4 convention or another approved 128-bit opaque identifier. Database row numbers and sortable tenant-global sequences are never public IDs. All timestamps are UTC RFC 3339 strings, for example `2026-08-08T12:34:56.123Z`; event-local rendering uses the event's stored IANA time-zone name.

## 2. API contract

### 2.1 Routing and media types

- Product endpoints live under `/api/v1`. `/health` may remain unversioned and reveal no dependency or configuration details.
- Requests and responses use `application/json; charset=utf-8`, except explicitly documented upload/download and calendar endpoints.
- A body-bearing JSON mutation rejects a missing or incompatible `Content-Type` with `415` and a body over the route limit with `413` before parsing.
- Unknown keys are rejected for authentication, authorization context, identifiers, state transitions, uploads, and other security-sensitive objects. Other request objects should also be strict unless forward compatibility is intentionally documented.
- Response DTOs are explicit allow-lists. Database rows are never serialized directly.
- `X-Request-Id` accepts a syntactically valid caller value or is generated at ingress. It is returned to the caller and becomes the correlation ID in logs, audit records, and durable delivery entries.

### 2.2 OpenAPI and validation

Each FastAPI route declares method, path, stable `operation_id`, dependencies/security, Pydantic request and response models, and all expected status codes. Use OpenAPI 3.1 and check a canonicalized, deterministically ordered `openapi.json` into the repository.

CI must:

1. Import the production FastAPI application in a build script, call `app.openapi()`, canonicalize the JSON, and fail on a checked-in `openapi.json` diff.
2. Lint the document with Redocly CLI using rules for unique operation IDs, declared security, and documented error responses.
3. Run contract tests that validate representative successful and error responses against the document.
4. Enumerate `app.routes` and fail if an `/api/v1` route is excluded from the schema or lacks a unique explicit operation ID, response model, security declaration, or shared error responses.

Pydantic validation occurs before authentication-dependent domain work. Models use `ConfigDict(extra="forbid", strict=True)` for security-sensitive inputs and explicit field/string/list limits, enums, numeric bounds, and identifier types. Normalization is performed by named validators and tested; validation does not silently discard input. FastAPI's default validation and exception responses are replaced centrally with the project's error envelope.

### 2.3 Status codes and error envelope

Use conventional semantics:

- `200` read/action success, `201` created, `204` mutation with no representation.
- `400` malformed syntax or invalid cursor, `401` unauthenticated, `403` authenticated but forbidden when disclosure is safe.
- `404` both absent and non-disclosed inaccessible tenant/owned resources.
- `409` state/version/conflict or idempotency-key payload mismatch, `412` failed conditional update, `413` too large, `415` unsupported media type, `422` schema/domain validation, `429` rate limited.

Every non-HTML API error has this envelope:

```json
{
  "error": {
    "code": "validation_failed",
    "message": "The request could not be processed.",
    "requestId": "01J...",
    "details": [
      { "path": "body.title", "code": "too_small", "message": "Required" }
    ]
  }
}
```

`code` is stable and machine-readable. `message` is safe for display and never contains SQL, stack traces, secrets, provider responses, account-existence clues, or private record data. `details` is present only for safe field-level validation. Production maps unknown failures to `internal_error` and `500`.

### 2.4 Lists, filters, and cursors

- Default page size is 25; maximum is 100 unless a route documents a smaller maximum.
- Sorts are allow-listed and deterministic, ending in the public ID as a tie-breaker.
- The cursor is an opaque, base64url-encoded, HMAC-authenticated payload containing API version, route/filter fingerprint, sort values, tie-breaker ID, and expiry. Invalid, expired, or filter-mismatched cursors return `400 invalid_cursor`.
- Keyset pagination is required; do not use client-visible offset pagination for product datasets.
- Every list query includes the authorized organization/event predicate and a matching index. Response shape is `{ "data": [], "page": { "nextCursor": null, "hasMore": false } }`.
- Filtering uses documented allow-listed fields and bounded values. Arbitrary query expressions and client-provided SQL field names are prohibited.

### 2.5 Concurrency and idempotency

Use conditional updates (`version` or `updated_at` precondition) for mutation races. Return `409 conflict` or `412 precondition_failed` consistently; the integrator must select one convention before feature implementation.

All retriable creates and action endpoints require `Idempotency-Key`, a 16–255 character high-entropy caller value. Scope a record by `(principal_id or public-session-id, organization_id, event_id, method, normalized_route, key)`. Store a SHA-256 hash of the canonical validated request, execution state, response status/body, created IDs, and expiry.

- The first request atomically creates the idempotency record, domain mutation, and any durable communication row in one D1 batch/transaction boundary.
- An identical completed retry returns the stored response with `Idempotency-Replayed: true`.
- Reusing a key with a different request hash returns `409 idempotency_conflict`.
- A concurrent in-progress duplicate returns `409 idempotency_in_progress` with bounded `Retry-After`.

Fanout actions such as publishing an agenda or adding submissions to an open
evaluation round store a scoped result and return it for an identical retry;
the retry must not enqueue calendar changes, assignments, or email again.
One-time-secret creation is different: only the secret hash is persisted. An
identical retry returns a stable `409` explaining that the original secret
cannot be replayed and must not create a second token.
- Retention is at least 24 hours and longer than the maximum client retry window; scheduled cleanup is indexed and bounded.

D1 prepared statements are mandatory. Cloudflare documents both [parameter binding](https://developers.cloudflare.com/d1/worker-api/prepared-statements/) and rollback behavior for failed [`batch()` transactions](https://developers.cloudflare.com/d1/worker-api/d1-database/).

## 3. Authentication and session security

### 3.1 Passwordless request and verification

`POST /api/v1/auth/magic-links` accepts a normalized email and an allow-listed relative return path. It always returns the same `202` envelope and comparable work regardless of whether the account is known. Provider delivery is asynchronous and does not change this response.

For an eligible identity, generate at least 256 random bits with `secrets.token_urlsafe(32)`. The emailed URL contains the raw token once; D1 stores only `hashlib.sha256(token_bytes).digest()` plus purpose, identity, requested path, creation/expiry, and nullable consumption timestamp. Use `hmac.compare_digest` for secret comparisons. The lifetime is 15 minutes. Never log the raw URL/token. Verification consumes the token exactly once in the same atomic boundary that creates the session; expired, unknown, consumed, or tampered tokens return the same generic failure. Apply both per-normalized-email and abuse-source rate limits without revealing which limit fired.

Return paths must be relative paths selected from an application allow-list; reject absolute, scheme-relative, encoded traversal, backslash, and control-character variants. Do not trust `Host` or forwarding headers to construct the canonical application origin.

### 3.2 Server-side sessions

Use an opaque 256-bit random session token. Store only its SHA-256 hash in D1 with user ID, issued time, idle expiry, absolute expiry, last-seen bucket, authentication method, rotation lineage, and revocation fields. The cookie contains a versioned token value authenticated with an application HMAC key; verification still requires the live D1 session record.

Cookie baseline: `__Host-session`, `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/`, and no `Domain`. Rotate the token after sign-in, privilege/membership changes, recovery, and periodically during an active session. Suggested policy: 12-hour idle expiry and 30-day absolute expiry, configurable downward. Rotation invalidates the predecessor after a brief bounded overlap only if necessary for concurrent browser requests.

Logout revokes the current session. Administrator security actions increment a user's or membership's authorization version and revoke matching sessions. Every protected request checks expiry, revocation, user state, and the authorization version needed for its context. Do not put roles or tenant grants solely in the cookie.

### 3.3 CSRF and browser-origin controls

Cookie-authenticated mutations require all of:

1. An exact allow-listed `Origin` (fall back to same-origin `Referer` only where browser behavior requires it).
2. A CSRF token sent in `X-CSRF-Token`, cryptographically bound to the current session and verified server-side.
3. A non-simple JSON content type for API mutations.

Rotate the CSRF token with the session. Safe methods must not mutate state. `SameSite` is defense in depth, not the sole CSRF control.

CORS is absent/deny-by-default for the same-origin web app. If a separate trusted UI origin is introduced, configure an exact environment-specific allow-list, explicit methods/headers, credentials only for those origins, `Vary: Origin`, and no wildcard origin with credentials. R2 has a separate minimal browser-upload CORS policy; Cloudflare notes that presigned browser requests require [bucket CORS](https://developers.cloudflare.com/r2/buckets/cors/).

## 4. Central authorization and tenant isolation

### 4.1 Policy interface

Authorization is a single application service, not route-local role checks:

```python
async def authorize(
    actor: Actor,
    permission: Permission,
    context: ResourceContext,
) -> AuthorizationDecision: ...
```

`actor` is derived only from the verified session. `permission` is a closed union of named capabilities (for example `event.settings.manage`, `submission.evaluate`, `speaker.asset.replace`). `context` contains server-resolved organization, event, resource owner, assignment, and lifecycle state. The default decision is deny. Decisions contain a safe reason code for audit/telemetry but handlers return the standardized non-disclosing API response.

Roles grant candidate permissions; they never prove resource access. A successful decision requires, as applicable:

1. active user and session;
2. active organization membership;
3. active membership/assignment for the resolved event;
4. named permission;
5. ownership or evaluator assignment for record-level access;
6. allowed resource lifecycle state (for example an open evaluation round).

Organization admin does not imply unrestricted cross-organization access. Event admin applies only to assigned events. Evaluator access is the intersection of active assignment, selected submission, and open round. Speaker access is limited to records connected to the verified speaker identity; request `userId` or email is never used as ownership proof.

### 4.2 Tenant-scoped data access

Protected domain repositories require an `AuthorizedScope` produced by the policy layer. There is no unscoped `findById` in feature code. Query methods take organization/event plus resource ID, and SQL repeats these predicates:

```sql
SELECT ... FROM submissions
WHERE organization_id = ?1 AND event_id = ?2 AND public_id = ?3
LIMIT 1
```

For evaluator/speaker reads, include assignment/ownership in the query or an `EXISTS` clause rather than fetching first and checking afterward. Writes use the same predicate and verify exactly one changed row. Foreign keys, uniqueness constraints, and composite tenant keys provide defense in depth. Dynamic SQL identifiers come only from immutable application-owned enum mappings; values use prepared bindings.

Return `404 resource_not_found` for cross-tenant IDs, unassigned evaluator resources, and another speaker's owned records. Use `403 forbidden` only when the resource's existence is already legitimately known and disclosure is safe. Tests must enforce this distinction and constant response shape.

### 4.3 Permission ownership

Maintain the permission catalog and role grants in one versioned Python module with a reviewable matrix test matching `requirements.md` Section 4.3. FastAPI dependencies resolve the authenticated `Actor` and `AuthorizedScope`; route functions pass that scope into async repository protocols. Feature modules may add resource facts and narrow a decision but cannot grant permissions, trust request tenant fields, instantiate an unscoped repository, or bypass scoped repositories. Sensitive changes to roles, memberships, assignments, decisions, schedules, assets, or outbound communications require a fresh policy evaluation inside the mutation boundary.

## 5. Audit, observability, and outbound boundaries

Audit records are append-only and contain: public audit ID, occurred-at UTC timestamp, actor type/ID, session ID hash or service identity, organization/event, named action, target type/ID, result (`allowed`, `denied`, `succeeded`, `failed`), safe reason code, request/correlation ID, and minimal structured change metadata. Never store tokens, cookies, raw form answers, biographies, email bodies, presigned URLs, or provider credentials. Audit authorization denials at the policy boundary and sensitive mutation outcomes after the transaction result is known.

Application logs are structured and separately redacted. Record route templates rather than resource URLs; do not log request/response bodies by default. Define retention and administrator access before production. Security telemetry includes login request/verification outcomes, CSRF/origin failures, repeated authorization denials, upload-signing abuse, rate-limit events, and audit-write failures.

Domain changes and outbound communications use a capability-owned durable message row written with the domain mutation. Consumers claim work idempotently using a deterministic message key. Webhooks verify the provider signature over the raw body, enforce timestamp/replay windows, and store provider event IDs uniquely before applying effects.

## 6. Rate-limit interface

Feature code depends on this interface rather than a Cloudflare binding directly:

```python
class RateLimiter(Protocol):
    async def check(
        self, policy: RateLimitPolicy, subject: RateLimitSubject
    ) -> RateLimitDecision: ...
```

Policies are named and centrally configured per environment, such as `auth.request`, `auth.verify`, `session.refresh`, `upload.sign`, `public.submit`, and `email.send`. Auth uses layered privacy-preserving subjects (normalized-email digest and abuse-source digest); authenticated routes prefer stable user/tenant IDs. Raw email and IP values must not appear in keys or logs.

Production uses Cloudflare's Workers Rate Limiting binding. It is low-latency but documented as local, permissive, and eventually consistent, so it is abuse mitigation—not an exact quota, uniqueness control, session lock, or billing counter. Exact single-use and idempotency guarantees remain in D1. Local tests use an injectable deterministic in-memory/fake-clock implementation. A denied request returns `429 rate_limited`, `Retry-After`, and safe machine-readable retry metadata without account disclosure. Emit telemetry on every denial. See Cloudflare's [Rate Limiting binding documentation](https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/).

## 7. Headers and response hardening

Apply headers centrally to HTML and API responses, with narrower endpoint overrides only when reviewed:

- `Content-Security-Policy`: begin with `default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'`; add nonce-based script/style directives and exact provider origins as the UI requires. Do not add `unsafe-inline`/`unsafe-eval` as a shortcut.
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: no-referrer`
- `Permissions-Policy`: disable unused camera, microphone, geolocation, payment, USB, and other capabilities.
- `Strict-Transport-Security` on production after HTTPS ownership is established.
- `Cache-Control: no-store` on authentication, session, private API, presigned-URL, and error responses containing user-specific state.
- `X-Frame-Options: DENY` as legacy defense alongside CSP `frame-ancestors`.

Implement one FastAPI/ASGI response-hardening middleware that applies this explicit policy after route execution, including error responses. Assert it in tests and remove technology-identifying headers. Public content gets an explicit cache policy; private responses never rely on CDN defaults. Middleware ordering is fixed and tested: request ID and safe error mapping wrap security headers, origin/CSRF checks, session resolution, route dependencies, and handlers.

## 8. Private upload authorization boundary

R2 buckets remain private. The upload flow is a capability issuance protocol:

1. An authenticated request asks `/api/v1/uploads/authorizations` for one declared purpose, parent resource, filename, media type, and byte size.
2. The server resolves the parent in its tenant scope, authorizes the purpose plus ownership, validates an allow-list and per-purpose size limit, creates an `upload_intent`, and generates an unguessable server-chosen object key. A client object key is never accepted.
3. Return a single-object, single-method presigned `PUT` URL with a maximum 10-minute expiry and signed `Content-Type`; treat it as a bearer secret and never log it. Cloudflare documents these constraints and recommends short expiries in its [R2 presigned URL guidance](https://developers.cloudflare.com/r2/api/s3/presigned-urls/).
4. The client uploads directly, then calls a completion endpoint with the intent ID—not an arbitrary object key. The server verifies object existence, declared size/type metadata, tenant binding, expiry, and unused state.
5. Mark the object `quarantined` and enqueue scanning/type verification. Do not trust filename extension or browser MIME. Only a clean current version can receive an authorized short-lived download URL.

Presigned URLs can be reused until expiry, so application completion must be one-time and object version changes after verification must invalidate the intent. Configure bucket CORS only for exact app/preview origins, `PUT`/`GET` as necessary, and the exact signed/request headers. Asset replacement retains prior metadata/audit history; deletion and quarantine are asynchronous, authorized actions.

## 9. Foundation Gate test matrix

Use `pytest`, `pytest-asyncio`, and `httpx` for pure Python unit and contract tests. Pure policy, Pydantic, cursor, signing, and repository-contract tests run quickly under CPython, but they are not sufficient evidence of Worker compatibility. A second integration tier starts the real application with `uv run pywrangler dev` and drives it over HTTP with `httpx`; it exercises Pyodide imports, ASGI adaptation, D1/R2/rate-limit bindings, middleware, and migrations. A final smoke/contract tier deploys to an isolated Cloudflare preview and repeats the Foundation Gate against preview bindings. Cloudflare documents `pywrangler` as the supported Python local-development and deployment wrapper in its [Python Workers package guide](https://developers.cloudflare.com/workers/languages/python/packages/).

The integration runner must allocate a unique local port and temporary persistence directory, wait on `/health`, capture sanitized logs, and terminate the child process even on failure. CI compares results from CPython and Pyodide tiers; behavior differences are defects. No test may pass solely because CPython offers native extensions, threads, a durable local filesystem, or synchronous network libraries unavailable in Pyodide.

| Area | Required tests | Required result |
| --- | --- | --- |
| Contract | Generate/lint OpenAPI; enumerate runtime routes; validate success and each shared error envelope | No undocumented route or schema/status drift |
| Parsing | Wrong content type, malformed JSON, unknown sensitive fields, oversized body, boundary lengths/counts | `413`/`415`/`422` as documented; no domain work |
| Anonymous access | Call every protected route without/with malformed cookie | `401`; no private data or existence signal |
| Magic links | Unknown/known email request comparison; expired, reused, tampered tokens; concurrent verification; redirect attacks | Generic request/verify response; exactly one session; no external redirect |
| Sessions | Rotation, idle/absolute expiry, logout, admin revocation, stale authorization version, cookie attributes | Old/revoked credentials fail; secure cookie baseline holds |
| CSRF/CORS | Missing/wrong token, disallowed/missing origin, simple content type, hostile preflight, allowed preview origin | Mutations denied; only exact configured origins accepted |
| RBAC matrix | Every role-permission cell from requirements, including deny-by-default unknown permission | Only documented grants succeed |
| Tenant isolation | Substitute organization/event/resource IDs in path, query, body, cursor, and idempotency scope | Consistent non-disclosing `404`; zero cross-tenant reads/writes |
| Evaluator scope | Assigned/unassigned submissions, another evaluation, closed round, revoked assignment, enumeration | Only active assignment and lifecycle permit access |
| Speaker ownership | Another speaker's profile, submission, task, asset and guessed IDs/list filters | `404`; public/private DTO boundary intact |
| Public serialization | Snapshot every public response using records populated with private fields | No contacts, notes, evaluations, tasks, object keys, or private URLs |
| Idempotency | Sequential/concurrent identical retry, changed-body reuse, different principals/events, consumer replay | One domain row and communication effect; correct replay/conflict behavior |
| SQL/data scope | Injection payloads, prepared-binding assertion, query-plan/index checks on large tenant fixtures | No injection; bounded indexed tenant query |
| Rate limits | Deterministic local thresholds/recovery; production binding smoke test; layered auth subjects | Predictable `429`/`Retry-After`; restart unnecessary; no disclosure |
| Audit/logging | Allowed/denied/failed sensitive actions and seeded canary secrets/private values | Complete actor/tenant/action/target/result; no canary leakage |
| Headers/cache | HTML, public API, private API, auth and errors | CSP/security policy present; private/auth responses `no-store` |
| Upload boundary | Cross-tenant parent, disallowed type/size, key substitution, expiry, replay, post-upload mismatch, quarantined download | No capability without authorization; only verified clean object downloadable |
| Performance | p75/p95 middleware timing, large authorized lists, denial paths, idempotent replay | Meets Section 8 budgets; no unbounded query or N+1 policy lookup |

Preview testing uses separate D1/R2/rate-limit namespaces and secrets, an exact preview origin allow-list, synthetic accounts, and no production provider credentials or data. The gate passes only when all tests pass both locally and in preview, with query plans and latency artifacts retained in CI.

## 10. Recommended dependency set

Keep the foundation small and pin reviewed versions:

- `fastapi` and `pydantic` v2: ASGI routing, request/response contracts, validation, and OpenAPI generation. Both are supported by Cloudflare Python Workers.
- `workers-py` and `workers-runtime-sdk`: `pywrangler`, Worker entrypoint/ASGI integration, and Python access to platform bindings.
- `pytest`, `pytest-asyncio`, and `httpx`: unit tests plus asynchronous HTTP contract/integration tests. Use `httpx` only in its async mode in Worker code.
- `ruff` and `mypy` (or Pyright): linting and static checking in CI; these are development tools and are not bundled into the Worker.
- `@redocly/cli`: OpenAPI linting in CI only; it is a build tool, not a Worker dependency.
- A small reviewed pure-Python SigV4 signer only if direct R2 presigning is selected and it passes Pyodide tests. Do not assume `boto3`/`botocore` works in the Worker or accept its bundle/startup cost without measurement; a Worker-mediated upload is the fallback.

Do not add a general-purpose authorization framework for MVP. Python `Enum` permissions, immutable dataclass/Pydantic context objects, pure policy functions, async tenant-scoped repositories, FastAPI dependencies that invoke them, and exhaustive matrix tests are simpler to audit for this role/resource model. Do not add a JWT library for browser sessions; server-side opaque sessions provide immediate revocation and avoid stale embedded role claims.

## 11. Decisions requiring integrator reconciliation

The integrator must settle these before scaffolding freezes shared contracts:

1. **Application framework:** confirm FastAPI/Pydantic v2 through Cloudflare's ASGI adapter is the Python Worker entrypoint and that all product data, including server-rendered UI mutations, goes through the registered `/api/v1` contract.
2. **Concurrency response:** choose `409` with a body version or HTTP conditional requests with `If-Match`/`412`; use one convention across features.
3. **Identity-to-speaker model:** define whether a verified user may control multiple speaker profiles and the explicit audited linking workflow; email equality alone must not grant ownership.
4. **Event-admin grants:** choose explicit event membership rows versus organization-role-derived grants. Explicit event grants are safer and match the requirements wording.
5. **Session timings:** approve the proposed 12-hour idle, 30-day absolute, and rotation/overlap windows based on event-operations needs.
6. **R2 signing:** choose a measured, reviewed Pyodide-compatible pure-Python SigV4 signer versus a Worker-mediated upload. Direct presigned uploads best protect Worker latency, but require S3 credentials in managed Worker secrets and carefully maintained bucket CORS; do not make `boto3` a default assumption.
7. **Malware scanning provider:** select the asynchronous scanner and define quarantine failure/timeout behavior before speaker asset work begins.
8. **Audit retention/access:** set retention, export, and authorized viewer policy before production; this document defines content but not organizational policy.
9. **Rate thresholds:** set per-environment thresholds after representative load tests. The interface and semantics are fixed; numbers are operational configuration.
10. **OpenAPI exposure:** decide whether production serves the document publicly, to authenticated admins, or only as a build artifact. Generation and CI verification remain mandatory.
