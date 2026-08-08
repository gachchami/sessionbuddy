# Nine-area delivery completion audit

Date: 2026-08-09

This record audits the delivery objective against current source, automated
verification, and the isolated Cloudflare development environment. It does not
equate development activation with approval for production promotion.

| Area | Current evidence | Status |
| --- | --- | --- |
| Production identity | Guarded, enumeration-resistant passwordless challenges; hash-only single-use tokens; opaque signed sessions; live D1 memberships; CSRF/origin enforcement; new- and existing-user invitation acceptance. `tests/security/test_production_identity_flow.py` exercises the production-mode HTTP journey. | Implemented and locally verified; the live provider accepted both administrator sign-in and an existing-user speaker invitation. |
| Initial bootstrap | `scripts/bootstrap_cloudflare.py` creates and removes its temporary secret without exposing it. Bootstrap is one-organization-only and accepts an organization/admin with no event. Live bootstrap created one organization without manufacturing an event; the administrator later created the rehearsal event and `BOOTSTRAP_TOKEN` remains absent. | Complete. |
| Deployable frontend | Product HTML/JS/CSS is same-origin and dependency-free; the evaluator workspace is React/TypeScript compiled by Vite and embedded into the Worker. TypeScript, Vite, asset-sync, Workerd routes, Lighthouse, and 28 authenticated/public desktop/mobile browser checks pass. | Complete. |
| Organization, event, and member administration | Organization rename, zero-event list/create state, event edit/archive, event navigation, role invitations, revocation, and member-role revocation have API and browser surfaces. The production identity flow creates the first event after an empty bootstrap, and the browser suite explicitly verifies the empty state. The live rehearsal created and edited `SessionBuddy Development Rehearsal`, round-tripped the organization name, revoked an evaluator invitation, accepted a speaker invitation, and confirmed that the sole administrator cannot revoke their own roles. Non-self member-role revocation remains covered by the local release suite. | Complete. |
| Speaker registration, invitation, and ownership | Published-form provisioning and admin invitation provisioning both require verified email. Tenant-owned person/event-speaker/submission records drive the portal; browser-supplied email never grants ownership. Regression coverage includes a new speaker and an existing user accepting an additional speaker role. | Implemented and live verified: the accepted speaker saw only the owned rehearsal proposal and assets. |
| Dynamic forms and drafts | Admins publish program-scoped versioned schemas with ordered fields and conditional visibility; server validation excludes hidden required fields. Verified speakers save optimistic-version drafts, resume them, submit idempotently, clear the draft, and see only owned submissions. | Implemented and live verified with a restored conditional Workshop draft, one owned submission, and zero remaining draft rows. |
| Deployment configuration | Pinned Docker, Node, Wrangler, uv, Python, compatibility date, D1/R2/Queue/Workflow/rate-limit bindings, secrets boundary, CORS file, migration commands, bootstrap, deploy, dry-run, and preflight commands are checked in and documented. | Complete for development. |
| Provider activation and Cloudflare rehearsal | Development D1/R2, strict R2 CORS, Queues/DLQs, consumers, Workflow, rate limits, HMAC secrets, Resend, and R2 upload credentials are active. Strict preflight reports 23 passed, 0 pending, 0 failed. Worker version `82c8daf7-9cc5-4c17-8324-0cabde887a25` has 100% traffic. Resend accepted real queued messages. The authenticated run completed empty-event administration, invitation acceptance/revocation, a conditional draft/submission, speaker ownership, and a direct R2 upload promoted clean under the explicit development scan bypass. | Complete for the isolated development environment. |
| Production hardening decisions/defaults | Passwordless identity, D1 authority, upload limits, scan policy, evaluation aggregate, agenda conflicts, retention safety, provisional capacity, fail-closed configuration, and provider/client promotion inputs are recorded in `platform-decisions.md` and `requirements.md`. Development scan bypass is explicit and audited; staging/production reject it. The R2-to-scanner boundary uses a fixed-length stream instead of buffering uploads in Worker memory, and the isolated scanner recomputes the claimed digest. | Application defaults complete; client/provider production-promotion inputs remain intentionally external. |

## Current verification evidence

- Full Python suite: 286 passed.
- Ruff: all checks passed.
- TypeScript check and Vite production build: passed.
- Browser/Axe: 28 passed across desktop Chrome and Pixel 7 profiles, including
  authenticated admin, access, evaluator, speaker, onboarding, and agenda pages.
- Large database release smoke: 10,000 submissions, 2,000 speakers, 50,000
  tasks, and 2,000 agenda items; integrity, foreign keys, schema hash, row counts,
  query plans, backup, and restore passed.
- Workerd smokes: CFP, fixed-length R2-to-scanner asset
  scan/download/reminder, and scheduling passed.
- Wrangler deployment dry run: passed; Worker bundle approximately 8.9 MiB and
  2.28 MiB gzip.
- Cloudflare activation preflight: 23 passed, 0 pending, 0 failed.
- Remote D1 rehearsal evidence for event
  `fcee42f3-a276-4906-aadc-8483df510d2b`: one active hybrid event in
  `Asia/Kolkata`; one revoked evaluator invitation; one accepted speaker
  invitation; active event-admin and speaker roles; one open program; one
  published version-1 form; one owned submitted proposal; zero draft rows; one
  1,732-byte `image/png` headshot version marked clean/current; audited create,
  acceptance, publish, submission, upload-authorization, and upload-completion
  actions; three event communication messages delivered.
- Deployed security headers allow `connect-src` only to the same origin and the
  configured account-scoped R2 S3 endpoint. Bucket CORS remains exact-origin,
  PUT-only, and Content-Type-only.

## Development rehearsal outcome

The authenticated rehearsal is complete. It exposed and resolved three live-only
integration defects: an asynchronous invitation-form reset, conditional draft
state not being recomputed after restore plus an invalid dynamic submission
payload, and CSP blocking the account-scoped R2 connection. Each repair has
automated regression coverage. The development event remains active as a reusable
rehearsal fixture. Two authorization attempts made before the CSP repair remain
non-current `pending_upload` versions; no failed version is clean or exposed by
the speaker asset list.

Production promotion additionally requires a verified sender domain for arbitrary
recipients, a production scanner endpoint/secret, approved retention and consent
wording, actual launch capacity estimates, and a production-like concurrency
baseline. Those are named external promotion inputs, not hidden application
defaults.
