# Sessionbuddy data architecture

Status: accepted platform foundation decision
Scope: P0 persistence foundation only  
Target: Python 3.13 on Cloudflare Workers, FastAPI/Pydantic, and Cloudflare D1 (SQLite semantics)

## 1. Goals and boundaries

D1 is the transactional source of truth for Sessionbuddy. The persistence layer must make tenant isolation, retriable writes, auditability, and asynchronous side effects structural properties rather than handler conventions.

This document defines the tables and rules needed before product feature work starts:

- organizations, events, programs, users, memberships, authentication challenges, and sessions;
- tenant keys and query-scoping rules;
- idempotency records, durable communication messages, and audit events;
- migration, seed, retention, and verification practices; and
- attachment points for later feature tables.

It intentionally does not define the complete submissions, forms, speakers, tasks, assets, evaluations, communications, agenda, or dashboard schemas. Those features extend this foundation through the rules in Section 12.

## 2. Database topology

Use one D1 database per deployed environment (`local`, each isolated preview, `staging`, and `production`). Use a shared-schema multi-tenant model inside each database, with `organization_id` and, where applicable, `event_id` stored on every tenant-owned row.

Do not create a database per organization for MVP. A shared database makes cross-organization administration, migrations, backups, local testing, and the initial operational model tractable. Isolation is provided by:

1. composite foreign keys that prove a child belongs to the same organization/event as its parent;
2. repository functions that require an authorized scope object;
3. tenant predicates in every protected statement; and
4. cross-tenant integration tests and query-plan checks.

Preview databases contain synthetic data only. Production data must never be copied into preview environments.

## 3. Storage conventions

### 3.1 Identifiers

- Generate UUIDv4 identifiers with Python 3.13's standard-library `uuid.uuid4()`.
- Store them as canonical lowercase `TEXT` values, including hyphens, and expose the same values as opaque API identifiers.
- Never use D1 `ROWID`, insertion order, an email address, or a slug as a public identifier.
- Generate all identifiers before beginning a write batch. This allows domain, audit, and capability-owned delivery rows to be committed atomically without reading generated keys.
- Do not encode tenant, entity type, or private information in an identifier.

UUIDv4 is the P0 choice because it is cryptographically random, opaque, available in Python 3.13's standard library under Pyodide, and avoids adding an identifier package to the Worker bundle. Python 3.13 does not provide standard-library UUIDv7. UUIDv7 may be adopted later only after a pure-Python/PyEmscripten-compatible implementation is pinned and its collision, clock rollback, and cold-start behavior are verified. The `TEXT` schema does not change if that happens. Authorization must never rely on identifier opacity.

### 3.2 Values

- Store instants as UTC Unix epoch milliseconds in `INTEGER` columns named `*_at_ms`. Serialize them at the API boundary as ISO 8601 timestamps with an explicit offset.
- Store an event's civil time zone separately as a validated IANA identifier in `events.time_zone`.
- Store booleans as `INTEGER NOT NULL CHECK (value IN (0, 1))`.
- Store bounded state values as `TEXT NOT NULL CHECK (...)`. State transitions remain domain logic and are tested explicitly.
- Store normalized email used for identity lookup separately from display/original email. Normalize by trimming and lowercasing the complete address; do not remove dots or plus tags.
- Prefer normalized columns and join tables over JSON for values used in authorization, filtering, uniqueness, or ordering. JSON is allowed only for bounded, versioned metadata that is not a tenant boundary or primary query predicate.
- Application writes set `created_at_ms` and `updated_at_ms`; do not depend on database wall-clock defaults for externally visible timestamps.
- Use optimistic concurrency on editable records through a nonnegative `version INTEGER` incremented by conditional updates (`WHERE id = ? AND version = ?`).

`organization_memberships.role = 'member'` is an internal affiliation marker,
not an application permission. It anchors event-scoped speakers, evaluators, and
event administrators to the organization that owns their event, satisfying the
tenant foreign-key boundary. It grants no UI or API capabilities and must not be
presented as an assignable user role. `organization_admin` is the only
organization-wide permission-bearing role.

### 3.3 Naming and constraints

- Use plural `snake_case` table names and `snake_case` columns.
- Declare every required value `NOT NULL` and use `CHECK` constraints for simple invariants.
- D1 enforces foreign keys. Specify `ON DELETE` explicitly. Business and audit records default to `RESTRICT`; short-lived authentication records may use `CASCADE` from their user.
- Every event-owned table carries both `organization_id` and `event_id`, even though the organization is derivable. This makes the tenant predicate mandatory, supports composite constraints, and yields useful indexes.
- Every program-owned table carries `organization_id`, `event_id`, and `program_id` for the same reason.

## 4. Initial P0 schema

The SQL below is illustrative contract SQL. The first implementation migration may add dialect-safe details, but it must preserve these keys and invariants.

```sql
CREATE TABLE organizations (
  id TEXT PRIMARY KEY NOT NULL,
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  status TEXT NOT NULL CHECK (status IN ('active', 'archived')),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  archived_at_ms INTEGER
);

CREATE TABLE users (
  id TEXT PRIMARY KEY NOT NULL,
  email TEXT NOT NULL,
  normalized_email TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active', 'suspended', 'deleted')),
  email_verified_at_ms INTEGER,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  deleted_at_ms INTEGER
);

CREATE UNIQUE INDEX uq_users_normalized_email
  ON users(normalized_email);

CREATE TABLE organization_memberships (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('organization_admin', 'member')),
  status TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  revoked_at_ms INTEGER,
  FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT,
  UNIQUE (organization_id, user_id)
);

CREATE TABLE events (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  starts_at_ms INTEGER NOT NULL,
  ends_at_ms INTEGER NOT NULL,
  time_zone TEXT NOT NULL,
  location TEXT,
  delivery_mode TEXT NOT NULL CHECK (delivery_mode IN ('in_person', 'virtual', 'hybrid')),
  description TEXT,
  logo_object_key TEXT,
  accent_color TEXT,
  status TEXT NOT NULL CHECK (status IN ('draft', 'active', 'archived')),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  archived_at_ms INTEGER,
  CHECK (ends_at_ms >= starts_at_ms),
  FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT,
  UNIQUE (organization_id, id)
);

CREATE TABLE programs (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  status TEXT NOT NULL CHECK (status IN ('draft', 'open', 'closed', 'archived')),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  archived_at_ms INTEGER,
  FOREIGN KEY (organization_id, event_id)
    REFERENCES events(organization_id, id) ON DELETE RESTRICT,
  UNIQUE (organization_id, event_id, id)
);

CREATE TABLE event_memberships (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('event_admin', 'evaluator', 'speaker')),
  status TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  revoked_at_ms INTEGER,
  FOREIGN KEY (organization_id, event_id)
    REFERENCES events(organization_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, user_id)
    REFERENCES organization_memberships(organization_id, user_id) ON DELETE RESTRICT,
  UNIQUE (organization_id, event_id, user_id, role)
);
```

An organization membership represents the user's relationship to the tenant. Event memberships narrow access within that tenant. A role alone is not permission to a record: evaluators still require a later evaluation assignment, and speakers still require a later ownership/person link. Organization administrators receive event access through policy evaluation and do not require duplicated event membership rows.

### 4.1 Authentication challenges and sessions

```sql
CREATE TABLE authentication_challenges (
  id TEXT PRIMARY KEY NOT NULL,
  normalized_email TEXT NOT NULL,
  token_hash BLOB NOT NULL,
  purpose TEXT NOT NULL CHECK (purpose IN ('sign_in', 'verify_email')),
  redirect_path TEXT NOT NULL,
  requested_ip_hash BLOB,
  expires_at_ms INTEGER NOT NULL,
  consumed_at_ms INTEGER,
  created_at_ms INTEGER NOT NULL,
  UNIQUE (token_hash)
);

CREATE TABLE sessions (
  id TEXT PRIMARY KEY NOT NULL,
  user_id TEXT NOT NULL,
  token_hash BLOB NOT NULL,
  csrf_secret_hash BLOB NOT NULL,
  created_at_ms INTEGER NOT NULL,
  last_seen_at_ms INTEGER NOT NULL,
  idle_expires_at_ms INTEGER NOT NULL,
  absolute_expires_at_ms INTEGER NOT NULL,
  revoked_at_ms INTEGER,
  revoke_reason TEXT,
  rotated_from_session_id TEXT,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (rotated_from_session_id) REFERENCES sessions(id) ON DELETE SET NULL,
  UNIQUE (token_hash),
  CHECK (idle_expires_at_ms <= absolute_expires_at_ms)
);
```

Challenge and session tokens are at least 256 bits from a cryptographically secure generator. Store only a keyed hash (or a SHA-256 hash for uniformly random 256-bit tokens) and compare fixed-length bytes. Never log tokens, hashes, cookies, or challenge URLs. Challenge consumption uses a conditional mutation requiring `consumed_at_ms IS NULL` and `expires_at_ms > now`; exactly one concurrent use may succeed. Session validation checks user status, revocation, idle expiry, and absolute expiry. Rotation creates a new session and revokes the old one atomically.

Authentication challenges deliberately do not reference `users`: issuing a link must not disclose or require account existence. The verification flow resolves or creates the user only after token possession is proven, subject to the chosen invitation/account policy.

## 5. Required indexes

Indexes are derived from actual access paths and kept narrow. Initial indexes:

```sql
CREATE INDEX idx_org_memberships_user_active
  ON organization_memberships(user_id, organization_id)
  WHERE status = 'active';

CREATE INDEX idx_event_memberships_user_active
  ON event_memberships(user_id, organization_id, event_id, role)
  WHERE status = 'active';

CREATE INDEX idx_events_org_status_updated
  ON events(organization_id, status, updated_at_ms DESC, id DESC);

CREATE INDEX idx_programs_event_status_updated
  ON programs(organization_id, event_id, status, updated_at_ms DESC, id DESC);

CREATE INDEX idx_auth_challenges_expiry
  ON authentication_challenges(expires_at_ms);

CREATE INDEX idx_sessions_user_active
  ON sessions(user_id, absolute_expires_at_ms)
  WHERE revoked_at_ms IS NULL;

CREATE INDEX idx_sessions_idle_expiry
  ON sessions(idle_expires_at_ms)
  WHERE revoked_at_ms IS NULL;
```

The primary key already indexes each `id`; do not add duplicate single-column ID indexes. Every cursor list index begins with its tenant equality predicates, then filter/order columns, and ends with `id` as a deterministic tie-breaker. New list endpoints must provide an `EXPLAIN QUERY PLAN` fixture proving indexed search rather than a full scan at the Section 8 dataset size.

After material index changes, run `PRAGMA optimize`. Revisit index write cost using D1 query metadata instead of adding speculative indexes.

## 6. Query scoping and repository contract

FastAPI handlers may not receive a raw database binding. A dependency resolves the Cloudflare environment from the ASGI request scope, authenticates and authorizes the request, then constructs repositories from the resulting immutable scope. Pydantic request models validate external input; persistence entities and authorization scopes are internal Python types, not request models that clients can populate.

```python
from dataclasses import dataclass
from typing import NewType

UserId = NewType("UserId", str)
OrganizationId = NewType("OrganizationId", str)
EventId = NewType("EventId", str)

@dataclass(frozen=True, slots=True)
class OrganizationScope:
    actor_user_id: UserId
    organization_id: OrganizationId
    permissions: frozenset[str]

@dataclass(frozen=True, slots=True)
class EventScope(OrganizationScope):
    event_id: EventId
    membership_id: str | None = None
```

Repository classes wrap the request's D1 FFI proxy and expose async, domain-named methods. The raw proxy stays private. Do not introduce a synchronous DB adapter or an ORM that assumes sockets, threads, a local filesystem, or native extensions.

```python
from typing import Any, Protocol

class D1PreparedStatement(Protocol):
    def bind(self, *values: object) -> "D1PreparedStatement": ...
    async def first(self, column: str | None = None) -> Any: ...
    async def run(self) -> Any: ...
    async def all(self) -> Any: ...

class EventRepository:
    def __init__(self, db: Any, scope: EventScope) -> None:
        self.__db = db       # Pyodide proxy for request.scope["env"].DB
        self.__scope = scope

    async def get_program(self, program_id: str) -> dict[str, object] | None:
        statement = self.__db.prepare(
            """SELECT id, name, status, version, updated_at_ms
               FROM programs
               WHERE organization_id = ?1 AND event_id = ?2 AND id = ?3"""
        ).bind(
            str(self.__scope.organization_id),
            str(self.__scope.event_id),
            program_id,
        )
        row = await statement.first()
        return None if row is None else row.to_py()
```

The exact FFI result conversion is centralized in one adapter and covered by runtime integration tests; route/domain code must not manipulate `JsProxy` objects. Convert result rows promptly with `to_py()` (or the equivalent supported conversion), validate the resulting mapping into an internal Pydantic model where useful, and destroy long-lived proxies when required by Pyodide guidance. Never perform blocking I/O in an async handler.

Repository methods accept the scope first and bind its IDs into SQL. Examples:

```sql
SELECT id, name, status, updated_at_ms
FROM programs
WHERE organization_id = ?1
  AND event_id = ?2
  AND (updated_at_ms < ?3 OR (updated_at_ms = ?3 AND id < ?4))
ORDER BY updated_at_ms DESC, id DESC
LIMIT ?5;

UPDATE programs
SET name = ?4, version = version + 1, updated_at_ms = ?5
WHERE organization_id = ?1 AND event_id = ?2 AND id = ?3 AND version = ?6;
```

Rules:

- Protected lookup/update/delete statements include all authorized tenant keys in the `WHERE` clause, even when `id` is globally unique.
- Event-owned resources are never fetched by `id` and authorization checked afterward.
- Repositories return the same not-found result for absent and out-of-scope rows. Policy maps it to the documented non-disclosing API response.
- List pagination uses an encoded, signed cursor containing the complete ordered tuple and query/filter version. Page sizes are allow-listed and capped.
- Search input is bounded. MVP must not rely on unindexed `%term%` scans over 10,000-row event datasets; adopt an explicitly tested search strategy when feature schemas are designed.
- Administrative/offline jobs also require an explicit scope or a separately named system capability; there is no unscoped general-purpose repository.
- Raw SQL is confined to the persistence package, migrations, and test fixtures, with a lint/review rule against direct `request.scope["env"].DB` access from FastAPI route handlers.

## 7. Transaction boundaries

D1 runs statements in autocommit mode; call the bound D1 proxy's async `batch()` method through the Python FFI for an atomic ordered transaction. If any statement fails, the complete batch rolls back. Do not emulate a transaction by awaiting several independent `prepare().run()` calls.

```python
statements = [
    db.prepare(INSERT_IDEMPOTENCY_SQL).bind(*idempotency_values),
    db.prepare(INSERT_DOMAIN_SQL).bind(*domain_values),
    db.prepare(INSERT_AUDIT_SQL).bind(*audit_values),
    db.prepare(INSERT_COMMUNICATION_SQL).bind(*communication_values),
    db.prepare(COMPLETE_IDEMPOTENCY_SQL).bind(*completion_values),
]
results = await db.batch(statements)
```

Keep each bound statement and its Python arguments alive until the awaited batch completes. The persistence adapter translates D1/FFI exceptions into a small internal error hierarchy; API handlers never expose raw JavaScript or SQL error messages.

A command batch contains only the consistency unit for one user-visible action:

1. create/reserve the idempotency record when required;
2. insert or conditionally update domain rows;
3. append the required audit event;
4. append durable capability-owned records for each asynchronous consequence; and
5. mark the idempotency record completed with its stable response reference.

All IDs and timestamps are prepared before the batch. Keep batches short and bounded; never send email, call R2, execute a Workflow, or perform network I/O within the database consistency unit. A zero-row optimistic update or state precondition is a command conflict, not success.

Agenda conflict writes will require their own feature-specific atomic design. The agenda agent must prove concurrent conflicting moves cannot both commit; a read followed by an unrelated autocommit write is unacceptable.

## 8. Idempotency records

```sql
CREATE TABLE idempotency_records (
  id TEXT PRIMARY KEY NOT NULL,
  principal_key TEXT NOT NULL,
  organization_id TEXT,
  event_id TEXT,
  route_key TEXT NOT NULL,
  idempotency_key_hash BLOB NOT NULL,
  request_fingerprint BLOB NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('in_progress', 'completed')),
  response_status INTEGER,
  response_resource_type TEXT,
  response_resource_id TEXT,
  created_at_ms INTEGER NOT NULL,
  completed_at_ms INTEGER,
  expires_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, event_id)
    REFERENCES events(organization_id, id) ON DELETE RESTRICT,
  UNIQUE (principal_key, route_key, idempotency_key_hash),
  CHECK ((state = 'completed') = (completed_at_ms IS NOT NULL))
);

CREATE INDEX idx_idempotency_expiry
  ON idempotency_records(expires_at_ms);
```

`principal_key` is a non-secret, stable namespace such as authenticated user ID or a server-derived public form/draft identity. The application hashes the supplied key and a canonical request representation. A repeated key with a different fingerprint returns a conflict. Do not store arbitrary response bodies or private form answers; store the status and stable resource reference, then rehydrate an authorized response.

For a new command, insert the idempotency row, domain row, audit row, and completion update in one batch. On a unique-key race, roll back and read the winning record. Because the transaction is atomic, another request never observes the inserted `in_progress` state from this normal path. Recovery handling for deliberately long-running commands belongs in their Workflow, not in an open database transaction.

Retention is route-specific and must exceed the maximum legitimate retry window. The default proposal is 24 hours for ordinary API mutations and through the form deadline plus 24 hours for final public submission keys. Product/security owners must approve final values.

## 9. Communication delivery ledger

`communication_messages` is the authoritative delivery ledger. Email-producing
commands insert a queued row as part of their database batch and then publish a
message identifier to Cloudflare Queues. A scheduled dispatcher republishes
aged queued rows, retryable failures after capped exponential backoff, and stale
consumer claims. Resend idempotency keys and conditional D1 claims make duplicate
queue envelopes harmless.

Queue publication failure never rolls back a committed domain change. Structured
dispatcher logs report recovery, publication failures, exhausted attempts, and
oldest pending age without including recipient or message content.

## 10. Audit events

```sql
CREATE TABLE audit_events (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT,
  event_id TEXT,
  actor_user_id TEXT,
  actor_type TEXT NOT NULL CHECK (actor_type IN ('user', 'system', 'anonymous')),
  action TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id TEXT,
  result TEXT NOT NULL CHECK (result IN ('succeeded', 'denied', 'failed')),
  reason_code TEXT,
  correlation_id TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata_json)),
  occurred_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, event_id)
    REFERENCES events(organization_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (actor_user_id) REFERENCES users(id) ON DELETE RESTRICT
);

CREATE INDEX idx_audit_event_time
  ON audit_events(organization_id, event_id, occurred_at_ms DESC, id DESC);

CREATE INDEX idx_audit_target_time
  ON audit_events(organization_id, target_type, target_id, occurred_at_ms DESC, id DESC);
```

Successful sensitive mutations append their audit event in the same transaction. Denied attempts and failures that have no domain transaction are appended using a separate best-effort security audit write and also emitted to redacted security telemetry. Audit metadata uses an action-specific allow-list and may contain changed field names or reason codes, but never tokens, cookies, email-link hashes, private form answers, asset URLs, message bodies, or full before/after records.

Audit events are append-only to application roles. Corrections are represented by another event. Audit retention and legal access policy remain an explicit product decision; until approved, do not automatically delete production audit events.

## 11. Migrations and recovery

- Check in ordered, immutable D1 SQL migrations managed through `uv run pywrangler d1 migrations ...` (Pywrangler forwards Wrangler commands). Use a timestamp plus descriptive name; never edit an already applied migration.
- CI creates an empty local D1 database, applies every migration, validates schema/foreign keys/indexes, seeds it, and applies migrations again to a disposable preview database.
- Use expand-and-contract changes: add nullable/defaulted structures, deploy dual-compatible code, backfill in bounded batches, switch reads/writes, then remove old structures in a later release.
- Every persistence change documents its roll-forward recovery. Application rollback must remain compatible with the expanded schema. Avoid destructive down migrations in production.
- Foreign keys remain enforced. During SQLite table-rebuild migrations, `PRAGMA defer_foreign_keys = ON` may defer checks only until transaction end; it is not permission to leave violations.
- Large backfills are resumable, cursor-based jobs (for example 1,000 rows per batch), not a single migration statement.
- Run `PRAGMA foreign_key_check`, representative `EXPLAIN QUERY PLAN`, and `PRAGMA optimize` as appropriate after schema changes.
- Use D1 Time Travel for operational point-in-time recovery within the plan's available window, plus the requirements' automated backup/export and quarterly restore exercise. A restore test records recovery point, recovery time, row counts, and application smoke results.
- Production migration order is expand schema, deploy compatible code, verify, then schedule any backfill/contract step. Never couple an irreversible schema contraction to the first code deployment.

## 12. Extension rules for product features

Later feature tables attach as follows without changing the foundation contract:

- **People and speakers:** `people` is organization-owned and may link to a verified `user_id`; event speaker participation is an event-owned join. Speaker-owned queries require the verified person/user ownership link, not merely an email match.
- **Forms and submissions:** form definitions and immutable versions are program-owned. A submission carries organization, event, program, form-version, and person/submitter references. Finalization uses idempotency and audit in one transaction; any notification is a durable communication row in that same consistency unit.
- **Tasks and assets:** event-owned, with explicit speaker/person ownership. D1 stores private R2 object keys and scan/version state only; bytes and signed URLs never enter D1.
- **Evaluations:** rounds are event/program-owned. Assignments connect evaluator membership to a submission. Authorization always joins or verifies the assignment inside the tenant boundary.
- **Communications:** messages are the durable delivery ledger and delivery attempts reference their deterministic keys. Provider callbacks are separately idempotent.
- **Agenda:** revisions and items are event-owned. Conflict protection and publication are transactional and auditable; asynchronous publication effects use capability-owned durable records.
- **Dashboard:** projections are rebuildable event-owned read models derived from authoritative rows. Push delivery is advisory; snapshot reads remain correct.

Every new event-owned table must include:

```sql
organization_id TEXT NOT NULL,
event_id TEXT NOT NULL,
FOREIGN KEY (organization_id, event_id)
  REFERENCES events(organization_id, id) ON DELETE RESTRICT
```

Program-owned tables additionally use a composite foreign key to `programs(organization_id, event_id, id)`. Add uniqueness constraints with tenant keys even when IDs are globally unique whenever the constraint expresses a tenant-domain invariant.

## 13. Seed strategy

Provide a first-party deterministic seed command independent of the private harness.

- A fixed seed value produces stable UUIDs, timestamps, names, role assignments, and record distributions.
- Named small scenarios cover two organizations, at least two events per organization, all roles, revoked memberships, expired/reused challenges, active/revoked sessions, and intentionally similar resource IDs/names that expose missing tenant predicates.
- The large scenario matches requirements Section 8: one event with 10,000 submissions, 2,000 speakers, 50,000 tasks, and 2,000 agenda items once those schemas exist.
- Test credentials and emails use reserved/example domains and never resemble production secrets.
- Seed execution is environment-gated and refuses production unless a separately designed, explicit administrative mechanism is used.
- Loading is batched below D1's bound-parameter and query-duration limits. Seed scripts are rerunnable by deterministic natural seed keys or reset only disposable databases.

## 14. Deletion and retention posture

The final retention schedule and privacy/legal requirements are unresolved in `requirements.md`. Until they are approved:

- archive organizations, events, and programs; do not cascade-delete business history;
- revoke memberships and sessions rather than deleting their security history;
- support user deactivation immediately, while deletion/anonymization is a separately audited workflow that preserves referential and legally required records;
- delete expired authentication challenges, expired sessions, and expired idempotency records in bounded scheduled batches;
- retain communication messages and delivery attempts long enough for operational investigation, then remove or compact delivered payloads under an approved policy;
- retain audit events indefinitely pending the approved policy; and
- treat R2 object retention separately from D1 metadata, with deletion jobs idempotent and reconciliation able to find orphaned metadata/objects.

Any hard-delete job must declare scope, emit an audit event, be restartable, use bounded batches, and verify no cross-tenant deletion. Legal hold must override ordinary expiry once that capability is introduced.

## 15. D1-specific constraints

The design accounts for current documented D1 behavior:

- Python Workers run Python 3.13 in Pyodide. D1 is accessed asynchronously through the JavaScript FFI binding exposed as `request.scope["env"].DB` in FastAPI or `self.env.DB` in a `WorkerEntrypoint`; there is no network D1 driver or connection pool.
- D1 uses SQLite semantics and enforces foreign keys by default.
- The D1 binding's awaited `batch()` call is the application transaction mechanism: statements execute sequentially and a failure rolls back the batch.
- Each database is limited to 10 GB on Workers Paid and 500 MB on Free; the proposed load dataset and indexes must be measured, not assumed to fit forever.
- A statement supports at most 100 bound parameters, a row/string/BLOB is limited to 2 MB, a query is limited to 30 seconds, and a Worker invocation has D1 subrequest limits. Chunk seed, cleanup, and backfill work accordingly.
- Indexes reduce rows read but increase rows written and storage. Use partial/composite indexes only for demonstrated access paths.
- Read replicas are eventually consistent. Authentication, authorization membership resolution, read-after-write redirects, idempotency resolution, conflict checks, and other correctness-sensitive paths read from the primary/session path with the required consistency. Replica use, if enabled later, is restricted to explicitly stale-tolerant read views with snapshot reconciliation.
- D1 Time Travel is recovery tooling, not application versioning or a substitute for tested exports/restores.
- Store files in R2, not D1, and keep communication/audit payloads bounded well below row limits.

Current limits must be rechecked against official Cloudflare documentation before production provisioning because plan limits and platform capabilities can change.

## 16. Persistence verification gate

The foundation is ready for feature work only when automated tests prove:

1. all migrations apply from empty storage and schema objects match the expected manifest;
2. foreign-key and `CHECK` constraints reject cross-organization event/program relationships and invalid states;
3. repository APIs cannot be constructed without a validated organization/event scope;
4. changing organization/event/resource identifiers cannot expose or mutate another tenant's rows;
5. membership revocation immediately removes database-backed access;
6. two consumers of one authentication challenge yield exactly one successful consumption;
7. revoked, idle-expired, absolute-expired, and rotated sessions fail validation;
8. two identical idempotent commands create one domain row, one audit event, and one communication message, while a changed payload conflicts;
9. a forced failure in any command-batch statement rolls back domain, audit, communication, and idempotency writes together;
10. a Queue publish crash/retry can publish more than once but the test consumer performs its external effect once;
11. audit metadata redaction fixtures reject prohibited keys/content;
12. cursor pagination has no gaps or duplicates for a stable dataset and is tenant bounded;
13. large-list `EXPLAIN QUERY PLAN` fixtures use the intended indexes and measured query timing meets the applicable budget;
14. deterministic small and large seed runs produce the expected counts and tenant traps;
15. backup/export and restore into an isolated database pass integrity, row-count, and application smoke checks; and
16. cleanup jobs delete only expired eligible rows in bounded batches.

Use three Python-first test layers:

1. **Pure tests:** `uv run pytest` tests Pydantic validation, permission/scoping construction, cursor encoding, canonical request fingerprints, redaction, and SQL-selection logic without a Worker runtime. A local `sqlite3` database may test basic constraints quickly, but it is only a test double.
2. **Local runtime integration:** a pytest session fixture applies migrations to an isolated local D1 database, launches `uv run pywrangler dev` on an allocated loopback port, waits for its health endpoint, and exercises FastAPI over async `httpx`. Tests access D1 only through application/test-support HTTP endpoints, so the actual Pyodide FFI, binding, prepared statements, result conversion, and batch rollback behavior are covered. Each test receives a unique tenant or disposable database state and teardown never targets a shared/production database.
3. **Preview integration:** the same black-box pytest contract suite runs against an isolated deployed preview URL and synthetic D1 binding. Destructive setup endpoints are protected by a preview-only secret/capability and do not exist in production builds.

`pytest-asyncio` (or pytest's selected async plugin) owns the async event loop. Never mock an awaited D1 call with a synchronous object. Pin Python, `workers-py`/Pywrangler, FastAPI, Pydantic, pytest, and HTTP client versions in `uv.lock`. CI commands are standardized as:

```bash
uv sync --frozen
uv run pytest tests/unit
uv run pywrangler d1 migrations apply sessionbuddy-local --local
uv run pytest tests/integration --run-worker
```

Exact CLI database names/flags live in checked-in scripts so CI and developers do not accidentally address remote or production D1. Run the same persistence suite against local D1 and an isolated Cloudflare preview. CPython `sqlite3` tests do not replace Python Worker runtime integration tests.

## 17. Decisions requiring lead reconciliation

1. **Membership shape:** this proposal requires every event member to have an organization membership and allows multiple event roles. Confirm whether invited speakers/evaluators should receive an implicit organization `member` row or whether event memberships should reference users directly.
2. **User versus person:** confirm that `users` is authentication identity and a later organization-owned `people` record holds speaker profile/PII. Avoid putting speaker profile fields on `users`.
3. **Identifier evolution:** UUIDv4 from Python 3.13 is selected for P0. Revisit UUIDv7 only if measured UUIDv4 index locality becomes material and a Pyodide-compatible implementation passes runtime tests.
4. **Database topology:** confirm one shared production D1 database for MVP. Database-per-tenant is explicitly rejected for the initial architecture.
5. **Authentication provisioning:** decide whether a verified unknown email creates a user automatically, requires a speaker submission/invitation link, or is rejected with a non-disclosing response.
6. **Retention:** approve exact lifetimes for sessions, challenges, idempotency records, delivered communication rows, audit events, and user deletion/anonymization.
7. **Read replication:** keep disabled or primary-consistent for foundation correctness paths; decide later whether stale-tolerant public/admin reads justify enabling it.

## 18. Official references

- [D1 database API and atomic batches](https://developers.cloudflare.com/d1/worker-api/d1-database/)
- [D1 foreign-key behavior](https://developers.cloudflare.com/d1/sql-api/foreign-keys/)
- [D1 index guidance and query-plan verification](https://developers.cloudflare.com/d1/best-practices/use-indexes/)
- [D1 platform limits](https://developers.cloudflare.com/d1/platform/limits/)
- [D1 global read replication consistency](https://developers.cloudflare.com/d1/best-practices/read-replication/)
- [Python Workers and Pywrangler](https://developers.cloudflare.com/workers/languages/python/)
- [Query D1 from Python Workers](https://developers.cloudflare.com/d1/examples/query-d1-from-python-workers/)
- [Python Workers FFI](https://developers.cloudflare.com/workers/languages/python/ffi/)
- [FastAPI on Python Workers](https://developers.cloudflare.com/workers/languages/python/packages/fastapi/)
