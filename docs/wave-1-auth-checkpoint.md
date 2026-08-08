# Wave 1 authentication checkpoint

The Wave 1 local CFP journey now crosses the production-shaped authentication
and authorization boundary. The local bootstrap is synthetic, but the session
created by it is persisted in D1 and all subsequent admin access uses the same
opaque-cookie, live-session, membership, permission, origin, and CSRF checks
required by future product routes.

## Implemented

- HTTP-only opaque local session cookie with a signed, versioned value
- Hash-only D1 session token storage with idle, absolute, revocation, user-status,
  and authorization-version validation
- Organization and event roles resolved from active D1 memberships
- Central named-permission authorization with deny-by-default behavior
- Origin, JSON media-type, and session-bound CSRF validation for admin mutations
- Tenant-scoped submission listing and bounded membership/list queries
- Actor identity attached to successful program and form audit events
- Cloudflare Rate Limiting bindings on local session creation and public submission,
  using keyed subject digests rather than raw identity or source values
- Standard `429 rate_limited` envelopes with a machine-readable `Retry-After` header
- Session introspection, atomic rotation, server-side logout revocation, and lifecycle audits
- Local secrets loaded from the ignored `.dev.vars` file and excluded from the
  Docker build context

## Runtime evidence

`scripts/smoke_wave1.py` verifies through Workerd/Pyodide/D1 that:

1. anonymous admin access returns `401`;
2. a cookie-authenticated mutation without CSRF returns `403`;
3. a cross-tenant identifier returns non-disclosing `404`;
4. an authorized organization administrator can create and publish a program;
5. a public proposal is idempotent and appears in the authorized admin list.
6. rotation revokes the old session and logout makes the cookie unusable.

## Deliberately next

The local bootstrap is not a production sign-in mechanism. The next identity
decision is whether the first production authenticator is a passkey, an external
identity provider, or an email challenge/recovery flow. Production HMAC secrets
must be installed through Wrangler rather than copied from local development.
