# Wave 3 speaker onboarding acceptance record

Status: local MVP acceptance complete; production provider activation pending

This slice establishes the security and persistence boundary for accepted-speaker
onboarding:

- A verified user is linked to a tenant-owned person, event-speaker record, and
  accepted submission through explicit foreign keys. Names and email equality
  never grant ownership.
- `/speaker` loads its event, profile, submissions, and at most 100 tasks from
  the authenticated actor. It accepts no user, tenant, event, or person identifier
  from the browser.
- Profile updates use CSRF, strict schemas, optimistic versions, and idempotency.
  A successful biography update completes the linked profile task in the same D1
  batch and records one safe audit event and one outbox fact.
- Foreign-owned and unassigned protected resources use the shared non-disclosing
  `404` authorization behavior.
- Portal and dashboard task queries have separate tenant-scoped indexes for the
  MVP load envelope.

The local-only speaker-session bootstrap exists solely to make this flow
reviewable before the production passwordless email provider is selected. It is
unavailable outside `APP_ENV=local`.

The second and third local slices add the operational dashboard and private
speaker assets:

- The admin dashboard uses explicit event scope, signed filter-bound cursors,
  bounded indexed snapshots, and a five-second refresh that pauses while hidden.
- Asset upload authorization validates actor-owned event/submission/task context,
  kind-specific MIME and byte limits, a client SHA-256 digest, CSRF, and
  idempotency before creating an opaque object key and hashed upload intent.
- Deployed environments generate a ten-minute, single-object R2 SigV4 PUT URL.
  Local Workerd uses a signed single-intent PUT adapter so byte length, MIME,
  checksum, private R2 storage, and completion can be tested without cloud keys.
- New versions remain non-current until scanning succeeds. The local Worker sends
  the quarantined bytes to an authenticated, isolated ClamAV container and only
  a signed clean verdict for the exact generation and checksum can promote it;
  deployed completion leaves it quarantined and emits a deterministic scan
  request. A replacement leaves the prior clean version current until promotion.
- Asset APIs expose allow-listed metadata only. They never serialize an R2 key,
  token hash, upload capability after authorization, or scanner payload.

The completed Wave 3 foundation also includes:

- Versioned Cloudflare Queue contracts and per-message acknowledgement/retry for
  asynchronous scanning and Resend delivery. Both consumers are replay-safe and
  persist bounded attempts and safe error codes.
- Strict escaped communication templates, confirmed recipient preview/manual
  send, delivery status, deterministic message keys, and a captured local
  delivery adapter.
- RFC 5545 invitations with stable UIDs, increasing sequence numbers, CRLF,
  escaping, and line folding.
- Durable reminder schedule/version persistence and a Python Cloudflare Workflow
  that sleeps until the scheduled instant, then emits the exact schedule version.
- Principal-bound, single-use, one-to-300-second download grants. Only the exact
  current clean version can be streamed, with private/no-store headers and no R2
  object key in the API contract.
- Five-second authoritative dashboard reconciliation with immediate same-browser
  invalidation, plus refresh on visibility, reconnect, and history restoration.
  Correctness does not depend on receiving a push notification.

The local acceptance smoke proves clean promotion and download, EICAR rejection,
and deterministic reminder queue/delivery through Workerd. Activating production
delivery requires creating the configured queues/DLQs and setting the Resend and
scanner secrets; those account operations are deployment configuration, not an
alternate application path.
