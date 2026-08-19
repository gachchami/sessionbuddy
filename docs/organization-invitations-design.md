# Organization administrator invitations

**Purpose:** Let an organization access manager (owner or `manage` admin) invite
another person to administer the organization and all of its events, with no
event required, and define how acceptance safely becomes `manage` access.
Closes the gap recorded in `org-collaborator-gap.md`.

**Status:** in-progress

**Authority:** The current schema, API contracts, authorization policy, and
executable tests outrank this document. `product-status.md` is authoritative
for shipped capability. Implementers must re-verify the tree (routes, migration
numbers, static assets, tests) before acting on any file reference below.

---

## 1. Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **One flow: always invite, invitee explicitly accepts.** Typing an email never grants access, whether or not the email already has an account. | RBAC rule: email matching alone never grants access; acceptance requires the recipient's own authentication. |
| D2 | **Owner and `manage` admins may create, resend, and revoke invitations.** Event-only authority is insufficient. | Same `RESOURCE_ACCESS_MANAGE` on `ResourceContext(organization_id)` that gates grants and ownership transfer today. |
| D3 | **New organization-scoped table `organization_admin_invitations`** with `permission` constrained to `manage`; the event-keyed `identity_invitations` schema is not weakened. | `event_id` is `NOT NULL`, part of both UNIQUE keys and the composite FK, and the table is a RESTRICT parent of `speaker_tasks` and `accepted_session_participants`, which migration `0004` already ruled out rebuilding. Separates organization administration from event invitations and from ownership. |
| D4 | **One canonical grant writer** shared by invitation acceptance and the existing-account grant path. | Code-quality rule: one derivation point per concept. Two near-identical SQL blocks exist today. |
| D5 | **Explicit acceptance for every recipient, new or existing.** Opening the link creates no account, consumes no credential, and grants nothing. | Scanners and prefetchers must not consume anything; consent must be an action. |
| D6 | **The immediate-grant create endpoint is retired in the same change** (see §7). | "Always invite" is not enforced while a direct-grant API remains; its only consumers are in-repo. |
| D7 | **Invitation status and delivery status are separate facts** and both are shown, as allowlisted values only. | "Pending acceptance" and "email delivery failed" can be true at once; internal diagnostics never reach the client. |
| D8 | **The inviter must still hold current organization access-management authority at acceptance and at resend** — owner, or an active `manage` grant — with the inviter's account active and the organization eligible, enforced in the same batch as the grant. | Removing an admin must not leave their outstanding invitations able to create new admins. Another active admin reissues if needed. |
| D9 | **Two credentials with two lifetimes.** The *invitation* and its link live **3 days from creation**; the invitation token identifies the invitation and proves nothing about the reader. Email ownership is proven only by fresh authentication: a separate invitation-bound **15-minute** verification challenge, available to **both new and existing users**, or an existing user's own password/magic-link sign-in. **Resend rotates credentials and never extends the original deadline.** An expired invitation needs a new invitation. | A long-lived credential must not authenticate anyone, new or existing. Fresh proof of control of the invited email is authentication; the three-day token is not. Explicit POST protects against scanner consumption; it does not replace short-lived identity verification. |
| D10 | **Invitation tokens never appear in request URLs.** The landing page is fragment-based; the token travels only in redacted POST bodies. | Keeps the token out of access logs, referrers and browser history beyond the fragment. |
| D11 | **Reissue is an explicit atomic operation** that revokes a pending invitation and creates its replacement attributed to the current admin. | The single-pending-row rule and duplicate-create otherwise block replacing an invitation whose inviter lost authority. |

## 2. User-facing flow

Organization settings → **Organizers** section.

- Form label changes from *Add admin* to **Invite admin**. Help text under the
  email field: *They'll receive an invitation to administer this organization
  and all its events. They don't need an existing account. The invitation is
  valid for 3 days.*
- On success: *Invitation created for x@y. Email queued.* Never *Admin added*
  before acceptance; never *Email delivered* merely because the message was
  queued.
- Two lists:

| List | Shows | Actions |
|---|---|---|
| Active admins | name/email, Owner or Admin | Revoke access (existing); ownership transfer (existing, owner only) |
| Invitations | email, invitation status (Pending · Accepted · Declined · Revoked · Expired), a **Needs reissue** marker on pending rows whose inviter no longer has authority, expiry, delivery status | Resend (pending, unexpired, inviter authority intact), Revoke (pending), **Reissue** (pending with *Needs reissue* — one atomic operation, §4), **Invite again** (expired/declined/revoked — a plain create with a fresh 3-day window), two-step confirm as elsewhere |

- Stable causes are rendered as distinct sentences (§6). The current
  "No active SessionBuddy account uses that email" mapping disappears with the
  old form.

Invitation page (`/organization-admin-invitations`, a static GET; the token
is carried only in the URL fragment `#token=…` and read by page script,
which resolves it with a POST — D10): organization name, *You are invited to
administer **<org>** and all of its events as an Admin (not owner)*, the
expiry, and the current state in plain words — pending; expired; revoked;
declined; accepted; *the admin who invited you no longer manages this
organization*. A token that no longer resolves (rotated by resend, or
unknown) shows the generic *This invitation link is no longer valid*. Then
exactly one of:

- **Signed in as the invited email** → *Accept invitation* / *Decline*.
- **Signed in as a different email** → *This invitation is for a different
  account* with the existing continue/sign-out choice; nothing else.
- **Not signed in** → *Email me a verification link* (works for new and
  existing accounts, §5) and, for people who have one, *Sign in with your
  password instead*. Whether an account exists is never shown. The invitation
  destination is preserved through either route.

New-recipient sequence, always in this order: **verify** (redeem the
15-minute link) → **register** (first name, last name, password, optional
job title/company — *Create account and continue*; this creates the account
and a session with no access) → land back on the invitation page → **Accept
invitation** (a separate POST). Existing recipients: verify or sign in → land
on the invitation page → *Accept invitation*.

Account page: pending organization invitations render beside pending event
invitations, with organization name, expiry, Accept and Decline.

## 3. Persistence

Deliver as the next unused incremental migration (verify the tree; `0006` is
free at the time of writing) plus its `checksums.sha256` entry. The released
baseline is not edited.

### `organization_admin_invitations`

| Column | Notes |
|---|---|
| `id` | PK |
| `organization_id` | NOT NULL, FK → `organizations(id)` |
| `email`, `normalized_email` | NOT NULL; canonical `normalize_email` |
| `permission` | NOT NULL, CHECK `='manage'` |
| `status` | CHECK IN (`pending`,`accepted`,`revoked`,`expired`) |
| `declined_at_ms` | nullable; *declined* = `revoked` + marker, the `0004` convention |
| `invited_by_user_id` | NOT NULL, FK → `users(id)` |
| `accepted_by_user_id` | nullable, FK → `users(id)`; set on acceptance |
| `token_hash` | BLOB NOT NULL UNIQUE; hash of the current invitation token (same `hash_token` as elsewhere). **A non-authorizing lookup handle:** nothing that grants, verifies identity, creates a session or creates an account depends on it — every authorizing operation is gated on `status`, `expires_at_ms` and the predicates in §5. Rotated by resend (the old token then resolves to nothing → generic "no longer valid"); **retained** through accept/decline/revoke/expire so an old link can resolve to its terminal state and explain it. It still exposes invitation information, so it gets **sensitive-token handling**: never in URLs or logs (D10), redacted from request bodies in telemetry, `resolve` rate-limited per source and per token, never returned by any API. Follows the `submission_contributors.invitation_token_hash` storage precedent, **not** its acceptance semantics (that flow lets the token alone create an account, which D9 forbids here). |
| `superseded_by_id` | nullable, FK → `organization_admin_invitations(id)`; set when a Reissue (§4) revokes this row in favour of its replacement |
| `expires_at_ms` | `created_at_ms + 72h`, written once; no statement may increase it |
| `accepted_at_ms`, `revoked_at_ms`, `expired_at_ms` | state timestamps |
| `display_name` | DEFAULT '' (≤200), greeting only |
| `created_at_ms`, `updated_at_ms`, `version` | as elsewhere |

Constraints and indexes:

- Partial UNIQUE `(organization_id, normalized_email) WHERE status='pending'`.
  Different organizations invite the same email independently.
  Accepted/revoked/expired rows remain for audit; *Invite again* is a **new
  row**. Before inserting a replacement for an expired-but-still-`pending`
  row, the same batch transitions that row to `expired` (compare-and-set on
  `status='pending' AND expires_at_ms<=now`, guarded).
- Index `(organization_id, created_at_ms DESC, id DESC)`;
  `(normalized_email, status, expires_at_ms, id)`.
- Triggers: actor holds an active `organization_memberships` row for the org
  at insert and on update of `organization_id`/`invited_by_user_id`;
  `(status='accepted') = (accepted_at_ms IS NOT NULL)` and requires
  `accepted_by_user_id`; `(status='revoked') = (revoked_at_ms IS NOT NULL)`;
  `(status='expired') = (expired_at_ms IS NOT NULL)`; `declined_at_ms` only
  with `revoked`; `expires_at_ms > created_at_ms`; **`expires_at_ms` is
  immutable after insert** (update trigger aborts on change); `token_hash`
  may change only while `status='pending'` (resend); `superseded_by_id` only
  with `status='revoked'`.
- A pending row creates **no** user, membership, role, or grant.

### `organization_admin_invitation_write_guards`

Same shape as `identity_invitation_write_guards` (`applied_changes INTEGER
CHECK(applied_changes=1)`), inserted with `changes()` after every
compare-and-set so a zero-row update aborts the whole D1 batch.

### `authentication_challenges` (verification credential)

Add nullable `organization_admin_invitation_id` FK →
`organization_admin_invitations(id)`, CHECK that at most one of
`invitation_id` / `organization_admin_invitation_id` is set. Recreate the two
scope triggers (drop/create — triggers carry no data) with one added clause:
when `organization_admin_invitation_id` is set, the referenced row's
`organization_id` and `normalized_email` equal the challenge's and
`event_id` is NULL. Rows for this purpose: `purpose='verify_email'`,
`provisioning_context='invitation'`, `expires_at_ms = MIN(now + 15 min,
invitation.expires_at_ms)`. This is the **only** credential that proves
ownership of the invited address.

### Delivery record

`communication_messages` already allows `event_id NULL`. Invitation email:
key `organization-admin-invitation:{invitation_id}:{token_rotation}`, subject
*You are invited to administer <org> on SessionBuddy*, body links to the
invitation page with the raw invitation token in the URL fragment.
Verification email: key
`organization-admin-invitation-verify:{invitation_id}:{challenge_id}`, link to
`/auth/verify#token=…`. Delivery status is read by joining the most recent
message for the invitation prefix; add a covering index on
`deterministic_key` if the query plan shows a scan. Credentials are never
stored in plaintext anywhere other than the protected message body the
existing delivery path already handles; never in API responses, logs, or
audit metadata.

Migration obligations (AGENTS.md): additive only; fresh-install test; upgrade
from the preceding schema with populated data; second application is a no-op;
`PRAGMA foreign_key_check`; table/index/trigger counts; backup and rollback
note (application rollback — the new tables and column are inert to the
previous application). Existing event invitations, users, grants, speaker
tasks and participants are untouched.

## 4. API

All admin routes resolve authority through the existing
`_organization_access_control_context` (`RESOURCE_ACCESS_MANAGE`,
`ResourceContext(organization_id)`, CSRF/origin on mutations). Denials remain
404 without enumeration; application causes are explicit (§6).

| Route | Behaviour |
|---|---|
| `POST /api/v1/admin/organizations/{id}/admin-invitations` | Body `{email}` (+ optional `display_name`). Server-controlled expiry: `created_at_ms + 72h`. `Idempotency-Key` required. Owner or active `manage` admin for that email → 409 `already_admin`. Pending unexpired invitation exists → 200 with it, no new email (`duplicate_pending`). Otherwise, in one batch: expire a stale pending row if present, insert `pending` with a fresh `token_hash`, queue the invitation message, audit `organization.admin_invitation.create`; queue wake-up after commit. Response: invitation view + delivery — **no token, no link**. Per-actor rate limit (`org.admin_invitation.issue`). |
| `GET …/admin-invitations` | List with derived `status` (`declined` from marker; a `pending` row past expiry reads `expired`) and `delivery` `{status, reason, updated_at_ms}` — `status` ∈ `queued · sending · delivered · failed · cancelled · none`; `reason` mapped to `provider_unconfigured · provider_unavailable · rejected · exhausted · unknown`. No `last_error_code`, attempt counts or provider text. |
| `POST …/admin-invitations/{iid}/resend` | Pending, unexpired, and D8 satisfied for the *original* inviter, else 409 with the cause (`inviter_authority_lost` → use Reissue). One batch: new `token_hash` (the old token no longer resolves), consume every unconsumed verification challenge for the invitation, queue a new invitation message, audit `organization.admin_invitation.resend`. **`expires_at_ms` is not touched.** Rate-limited as create. |
| `DELETE …/admin-invitations/{iid}` | Pending → `revoked` (compare-and-set + guard), consume every verification challenge for the invitation, audit `organization.admin_invitation.revoke`. `token_hash` is retained as a lookup handle (§3). Accepted → 409 `invitation_already_accepted` (revoke access from Active admins instead). |
| `POST …/admin-invitations/{iid}/reissue` | Only for a `pending` row (expired or not). **One guarded batch, in this order:** (1) compare-and-set the old row to `revoked` (`superseded_by_id` still NULL) + guard; (2) consume its verification challenges; (3) insert the replacement `pending` row — **same `organization_id` and `normalized_email` as the old row, copied by subquery from the old row, not from the request** — with the **caller** as `invited_by_user_id`, a fresh `token_hash` and a fresh `created_at_ms + 72h`; (4) `UPDATE old SET superseded_by_id = new` + guard (the FK target now exists); (5) queue the invitation message; (6) audit `organization.admin_invitation.reissue` with `{superseded_invitation_id}`. The partial unique index is satisfied because (1) precedes (3). A trigger on `superseded_by_id` requires the referenced row to share `organization_id` and `normalized_email` and the updated row to be `revoked`. Same rate limit and `Idempotency-Key` rules as create. This is the required path when the list marks a row *Needs reissue*; it is also valid for any pending row an admin wants under their own authority. |
| `POST /api/v1/organization-admin-invitations/resolve` | Body `{token}` (redacted from logs like form content). Public read for the invitation page: org name, expiry, derived state, `needs_reissue`, and — only when the caller has a session — whether that session's email matches. Never whether an account exists for the invited email. Unknown/short token → 404 (`invitation_link_invalid`). Rate-limited per source. |
| `POST /api/v1/organization-admin-invitations/verification` | Body `{token}`. Outcomes, in order: unknown token → 404 `invitation_link_invalid`; row not `pending` or past expiry → 409 with the state cause (`invitation_expired` / `invitation_revoked` / `invitation_declined` / `invitation_already_accepted`); D8 fails → 409 `inviter_authority_lost`; **a live challenge already exists** for this invitation (unconsumed, unexpired) → **202 with nothing queued** — the email already sent is still valid, so retries and double-clicks produce exactly one verification email per 15-minute window (deduplication by state, enforced in the batch by an `INSERT … WHERE NOT EXISTS(live challenge)` followed by a write-guard on `changes()` and then the message insert keyed `…-verify:{invitation_id}:{challenge_id}`, so a message can never be queued without its challenge). **Concurrent loser:** when two requests race past the pre-read, the second batch's conditional insert changes zero rows, its guard aborts the batch *before* the message insert, and the handler catches that specific guard failure, re-reads the now-live challenge, and returns the same 202 — never a 5xx or a database error to the caller; only a guard failure with no live challenge on re-read is treated as an error); per-invitation issue limit exceeded (3 challenges per hour, counting only challenges actually created) → 429; otherwise **202** and one 15-minute challenge is queued to the invited address only. The response is identical for "queued" and "already live" so the caller cannot probe timing. Per-source rate limit as well. A new challenge may be issued only after the previous one is consumed or expired. |
| `GET /api/v1/account/invitations` | Adds `pending_organization_invitations: [{id, organization_id, organization_name, expires_at_ms, needs_reissue}]`; the event-keyed list and view model unchanged. |
| `POST /api/v1/account/organization-invitations/{iid}/accept` · `…/decline` | **The only accept/decline routes.** Authenticated session whose user's `normalized_email` equals the invitation's, else 404. CSRF, `Idempotency-Key`, fingerprint and session-replacement rules as `_account_invitation_action`. The invitation page calls these with the `id` returned by `resolve`; the id alone is not a credential because the email match is required. Accept runs §5. |

Cross-organization: every admin read and mutation binds `organization_id`
from the route; an invitation id from another organization is 404. Public
token routes reveal nothing about other invitations or account existence.

The organization activity projection gains sentences for the new audit
actions.

## 5. Acceptance and authorization

### How identity is established (never by the invitation token)

- **Existing account, has a password** → password sign-in.
- **Existing account, live context** (membership, assignment) → ordinary
  magic link.
- **Existing account, no password and no live context** (e.g. an offboarded
  admin being re-invited, or a CFP-only account whose credential was never
  set) → the ordinary magic-link endpoint deliberately sends nothing
  (`request_magic_link` inner-joins on live context; `product-status.md`
  records this). The invitation-bound **verification challenge** is the
  supported path: it is invitation-specific, delivered only to the invited
  address, 15 minutes, and its redemption establishes a session **as the
  existing user** exactly like a magic link — then the person still has to
  press *Accept*. This is consistent with the RBAC rule: the *invitation*
  token never mints a session; a short-lived, invitation-issued,
  email-delivered challenge is proof of email ownership.
- **No account** → verification challenge. **Registration proof is the
  challenge itself, consumed by the registration POST.** The *shape* follows
  the CFP path (`_requires_submission_registration` renders the fields on a
  non-consuming check; `POST /auth/verify` with registration fields redeems
  the token and creates the user), but the atomicity is **new work**: today
  `_redeem_magic_link` consumes the challenge in a first statement, then
  calls the finisher, and restores `consumed_at_ms` on failure only as a
  best-effort second write. The organization registration path must instead
  put consumption and provisioning in **one guarded D1 batch**. Concretely,
  three steps, because a GET cannot see a URL fragment: (a) `GET
  /auth/verify` renders the confirm shell with an empty token field (the
  existing `magic_link_interstitial`); (b) the existing confirm script moves
  `#token=` into the hidden field and the person presses the button — a
  **non-consuming POST** carrying the token in the redacted form body, which
  the server checks against `token_hash`, unconsumed and unexpired, and —
  because no `users` row exists for the challenge's email — answers by
  rendering the registration fields (for an existing account: the confirm
  button); this is what `_requires_submission_registration` does today with
  a `SELECT` only; (c) the registration POST carries the raw token again
  and, in one batch:
  consumes the challenge by compare-and-set (`consumed_at_ms IS NULL AND
  expires_at_ms>now`) followed by a write-guard on `changes()`, re-checks by
  predicate that the bound invitation is still `pending` and unexpired and
  that no `users` row exists for the email, creates the user (`active`,
  email verified, password credential) and a session **with no access**,
  then returns the person to the invitation page. Any failure aborts the
  whole batch — no consumed-but-unregistered challenge, no user without a
  consumed challenge. Properties, stated accurately: **short-lived** (the
  challenge's 15 minutes); **single-use** (consumed atomically with
  provisioning); a **bearer token proving control of the invited email** —
  it is *not* browser-bound: anyone holding the emailed token can redeem it
  in any browser, which is the accepted property of a magic link, and true
  browser binding would require an additional browser-held secret that this
  design does not add; **invalidated by any invitation transition**
  (challenge consumption on resend/revoke/reissue, plus the batch
  predicate). Knowing a challenge *id* enables nothing — the raw token is
  required and only its hash is stored. Acceptance remains a **separate**
  POST (safeguard 5). Registration details entered before verification never
  create anything: the fields are only rendered for a valid live challenge,
  and the POST fails closed (404, no user) if the challenge was consumed or
  expired meanwhile. For an existing account the same POST without
  registration fields consumes the challenge and establishes the no-access
  session in one batch under the same rule.
- `users.status` `suspended`/`deleted` → `account_unavailable`; an invitation
  never reactivates an account.

### Safeguards on the verification route (explicit, tested)

1. A verification challenge is bound to exactly one invitation and to the
   invited `normalized_email` (scope trigger, §3); it cannot be issued for,
   or redeemed against, any other address or invitation.
2. Redemption for a `suspended`/`deleted` account is refused before any
   write; no session, no reactivation.
3. Redemption proves email control and establishes a session **only**. It
   writes no `organization_memberships`, `user_roles`, or
   `resource_access_grants` row and touches no `authorization_version`. For a
   new email it creates the `users` row and password credential (registration)
   and nothing else.
4. The resulting session may belong to a user with **no workspace at all**
   (no membership, assignment or grant). The app shell must render a
   no-workspace state for that session — no redirect loop, no bounce to
   sign-in — from which the preserved invitation destination
   (`/organization-admin-invitations#token=…`, fragment preserved through
   the redirect) and the Account page's pending list are reachable. (The earlier onboarding-guard loop for an organizer
   persona with empty `organization_access` is the failure mode to test
   against.)
5. Acceptance is a separate POST that independently rechecks expiry,
   revocation, identity, organization eligibility and inviter authority,
   atomically (below). Verification never implies acceptance.
6. Resend invalidates every outstanding verification challenge for the
   invitation and rotates the invitation token; accept, decline, revoke and
   reissue consume every remaining verification challenge — after any
   terminal transition no *credential* for that invitation works. The
   invitation token is not a credential: it survives as a lookup handle that
   resolves only to the terminal state (§3), and the verification and
   acceptance predicates on `status='pending' AND expires_at_ms>now` make it
   inert.
7. Re-inviting an offboarded user restores **only** the explicitly accepted
   organization `manage` grant (plus the non-authorizing membership row and
   the organizer persona role). Grants on other organizations or events that
   were revoked stay revoked; the grant writer touches only the resource named
   by the invitation, and a test asserts other revoked rows are unchanged.

### Canonical grant writer

Extract `append_organization_manage_grant(batch, *, organization_id, user_id,
granted_by_user_id, now)` from the two current sites (the existing-account
grant helper and the `organization_admin` branch of the magic-link finisher).
It appends, in order: the non-authorizing `organization_memberships` `member`
row (upsert to active), revocation of any active non-`manage` grant on the
organization resource, the `manage` grant upsert, the `organizer` `user_roles`
row (default only if the user has no active role), and the
`users.authorization_version` bump. Every caller uses it; a parity test (§8)
guards drift. The magic-link finisher gains no mode flag: organization
acceptance is a separate thin function that builds a batch, calls the grant
writer, and hands the batch to the existing session-establishment helpers.
Session authentication stays separate from the authorization grant.

### Atomic acceptance (one D1 batch)

1. Compare-and-set: `UPDATE organization_admin_invitations SET
   status='accepted', accepted_at_ms=?, accepted_by_user_id=?,
   version=version+1 WHERE id=? AND status='pending' AND
   expires_at_ms>?` **AND** the predicates below, each an `AND EXISTS(...)`
   term; then a write-guard insert with `changes()` — zero rows aborts the
   batch.
   - organization `active` and its `owned_resources` row active;
   - accepting user `active` and `normalized_email` equal to the invitation's;
   - **inviter authority (D8):** `invited_by_user_id` is the organization's
     current `owned_resources.owner_user_id`, or has an active `manage` grant
     on the organization resource, **and** the inviter's `users.status` is
     `active`. The page pre-flight applies the same predicate so the
     recipient reads the explanation before submitting; a revocation between
     render and submit still loses in the batch.
2. Grant writer.
3. Consume any outstanding verification challenges for the invitation.
4. Audit `organization.admin_invitation.accept`, actor = accepting user,
   metadata `{inviter_user_id}` (no credentials).
5. Idempotency completion — replay with the same key and fingerprint returns
   the recorded 200; a different fingerprint is 409.
6. Session establishment or persona replacement only after the batch result
   confirms every statement applied. If the response is lost after commit, a
   retry with the same idempotency key returns the committed result and the
   grant writer's upserts add nothing.

When D8 fails: 409 `inviter_authority_lost`, *The admin who invited you no
longer manages this organization. Ask a current admin to send a new
invitation.* The row stays `pending` (listed as *Needs reissue*) until an
admin **reissues** it (§4) or it expires; nothing is granted. Because the
single-pending-row rule and duplicate-create would otherwise return this
very row, a plain create is **not** the recovery path — Reissue is. Resend
applies the same predicate and returns the same cause.

**Already an admin at acceptance** (owner, or an active `manage` grant
appeared after the invitation was sent) — an explicit branch, chosen by a
read before the batch and **re-verified inside it**: the compare-and-set to
`accepted` carries an extra `AND EXISTS(owner or active manage for this user
on this org)` predicate, then the guard, challenge consumption, the audit
record with `{already_admin: true}`, and idempotency completion. The grant
writer is **not** called, so `authorization_version` is not bumped and no
session is replaced. Response 200 `already_admin`. Both races are covered:
if the grant is **revoked** between the read and the batch, the predicate
fails, the guard aborts, nothing is recorded, and the handler re-evaluates
from a fresh read and runs the normal branch (which grants); if a grant
**appears** between a normal-branch read and its batch, the normal branch's
upserts are harmless and its version bump only forces a re-read of
authorization. Neither race can record `accepted` without access. Both are
tested with interleaved writes.

## 6. Retry, race, and failure behaviour

- Creation commits invitation, token hash, message and audit together; queue
  wake-up after commit; a wake-up failure leaves a recoverable `queued`
  message. No success message unless the batch result confirms the writes.
- Duplicate create while pending returns the existing invitation and sends
  nothing; same-key replay returns the recorded response.
- Resend replaces `token_hash` and consumes verification challenges in one
  batch — no window in which two invitation links or two verification links
  are live. **Deadline unchanged.**
- Revocation consumes challenges in its batch; the old link still resolves
  (lookup handle) and reads `invitation_revoked`. A link rotated away by
  resend resolves to nothing and reads the generic `invitation_link_invalid`.
- Reissue is one batch (revoke old + insert new + queue + audit); a failure
  anywhere leaves the old row `pending` and no replacement.
- Acceptance vs revocation, and concurrent acceptances: compare-and-set on
  `status='pending'` with guards; exactly one wins.
- Expiry: readers derive `expired` from the clock; every acceptance and
  verification predicate includes `expires_at_ms>now`; a verification link
  cannot be redeemed after the invitation expires even if its own 15 minutes
  remain. A stale `pending` row is transitioned to `expired` when *Invite
  again* creates its replacement. Purge of old rows is out of scope (the
  existing no-purge finding applies).
- Delivery: allowlisted `delivery.status`/`reason` only; `failed` shows
  *Email could not be delivered — resend* without changing the invitation's
  own state.

Stable causes (in the error envelope, rendered by the client instead of
interpreting HTTP status): `invitation_expired`, `invitation_revoked`,
`invitation_declined`, `invitation_already_accepted`,
`invitation_identity_mismatch`, `inviter_authority_lost`, `already_admin`,
`duplicate_pending`, `account_unavailable`, `verification_expired`,
`invitation_link_invalid`, `invitation_delivery_unavailable` (503 when `PUBLIC_BASE_URL` is neither
https nor local, matching `_append_invitation_link`).

## 7. Compatibility and retirement

**Event-keyed `organization_admin` invitations.**

- `POST /api/v1/admin/events/{id}/invitations` stops accepting
  `organization_admin` → 422 `use_organization_admin_invitations`.
- Already-issued rows keep working unchanged with their **original expiry**:
  list, resend (rotates only the 15-minute challenge; verified in
  `resend_invitation`, which never writes `expires_at_ms`), revoke, account
  accept/decline, and link redemption. No migration alters their deadlines —
  an environment upgraded late must not have legitimate invitations expired
  by the upgrade.
- The deadline is nonetheless bounded: with creation refused and resend
  non-extending, no pending legacy row can outlive its recorded
  `expires_at_ms` (≤ 30 days from its creation). `MAX(expires_at_ms)` over
  pending legacy rows per retained database is a **planning estimate** only.
- Retirement gate: a **fresh** zero-count check of eligible pending legacy
  rows (`role='organization_admin' AND status='pending' AND
  expires_at_ms>now`) in every retained database, run at the time of the
  retirement change — not the earlier estimate. Only then does a later change
  remove the role from `InvitationRole`, the org-admin branch of the
  finisher, and the `_INVITATION_PERSONAS['organization_admin']` entry. Not
  part of this change.
- The legacy path still mints a session for an existing user from its
  invitation-issued challenge. That remains a separately tracked security
  concern; this document does not describe it as fixed.

**Immediate-grant endpoint `POST …/organizations/{id}/access-grants`.**
Consumers found in the tree: `organization_admin.js` (the form being
replaced); a dead `organizationMode` branch in `account.js` (that script is
not served at `/admin/organization` — remove the branch); three test modules
(`test_resource_control_plane`, `test_revoked_grant_reauthentication`,
`test_organizer_workflow`); Playwright route mocks in
`account-responsive.spec.ts` and `forms.spec.ts`. No external consumer is
known. **Retire the POST**; keep `GET` (list) and `DELETE` (revoke) for the
Active admins list. Tests that used the direct grant as a fixture move to an
"invite + verify + accept" helper. If the team prefers to keep the POST
temporarily, that is a documented policy exception: mark it superseded in
OpenAPI, restrict it to owner-only, and record the removal condition in
`product-status.md` — D1 is not honoured until it is gone.

**Inviter revocation** is enforced at acceptance and resend (D8); no cascade
is needed for safety. The list derives *Needs reissue* for such rows and the
Reissue operation (§4) is the recovery path.

## 8. Verification

Tests must reach the behaviour they name and fail when the defect is restored
(AGENTS.md review discipline). Source-text tests are labelled `wiring`.

Schema: fresh install; upgrade from the preceding schema with populated data;
double apply no-op; `foreign_key_check`; counts; trigger rejects an
invitation whose actor has no active membership; partial unique rejects a
second pending row; `expires_at_ms` update is rejected; challenge CHECK
rejects both invitation references; scope trigger rejects a mismatched
`organization_admin_invitation_id`.

HTTP: owner and `manage` admin create/list/resend/revoke; event-only admin,
speaker, reviewer and outsider get 404; **organization with no events**
creates an invitation; new email needs no pre-existing account; pending
invitation grants nothing (`/auth/session` for the invitee unchanged);
event-keyed `organization_admin` → 422; cross-org id → 404 for every verb;
public token routes never reveal account existence; no response body ever
contains a token or link; create without `Idempotency-Key` → 400; replay
returns the recorded body.

Identity and acceptance: loading the landing page and calling `resolve`
consume nothing and create nothing (assert row counts before/after); the
token never appears in any request URL (wiring test over the two static
scripts and a request-log assertion in the Worker smoke); invitation token
POSTed as if it were a challenge → 404; verification challenge older than
15 minutes → `verification_expired`; verification within 15 minutes but
invitation past 72 h → `invitation_expired`; new user — verify, register
(assert: user + credential + session exist, zero grants, `/auth/session`
shows no organization access), then accept → one user, one `manage` grant,
organizer session; registration POST without a redeemed challenge → 404 and
no user; existing user with password — sign in,
accept → same user id, no duplicate; existing user with no password and no
live context — ordinary magic link sends nothing, invitation verification
signs them in, accept works; wrong account → `invitation_identity_mismatch`,
no grant; suspended → `account_unavailable` on verification and on
acceptance. Safeguard tests (§5): verification redeemed → zero new rows in
`organization_memberships`, `user_roles`, `resource_access_grants`, unchanged
`authorization_version`; a verification challenge for invitation A cannot be
redeemed after A is resent, accepted, declined or revoked; a no-workspace
session renders the shell and can open the invitation page and Account
pending list (extracted-shell harness iterated over the persona states);
re-invite of an offboarded user leaves their revoked grants on other
resources byte-identical.

Races and failures: concurrent accept/revoke — exactly one applies, zero
residue; resend then old invitation link → `invitation_link_invalid` and old
verification link → 404; resend leaves `expires_at_ms` byte-identical;
revoke then old link → resolves and reads `invitation_revoked`, verification
→ 409, accept → 404; reissue — old row `revoked` with `superseded_by_id`, new
row `pending` under the caller, old token resolves to revoked, new token
works, a forced failure mid-batch leaves the old row `pending` and no new
row; already-admin acceptance → single grant row, `authorization_version`
unchanged, audit carries `already_admin: true`; a forced
batch failure after the compare-and-set leaves the row `pending` and no
partial rows; queue wake-up failure leaves a `queued` message the dispatcher
later delivers; response lost after commit → idempotent retry returns the
committed result with no second grant; inviter revoked between render and
submit → zero statements applied, `inviter_authority_lost`. For each guard,
remove it locally and confirm the test turns red, then restore it.

Grant-writer parity: after acceptance the org's access-grant list and the
invitee's `/auth/session.organization_access` equal what the direct grant
produced before its retirement; a mutation of the writer turns both red.

### In-app browser acceptance gate

Three layers, all required; none substitutes for another:

1. **Automated Playwright specs** (real local Worker, D1, Mailpit — never
   mocked invitation/auth endpoints) and the **extracted-shell harness** for
   persona-state coverage. These are the regression floor and run in the
   release gate.
2. **The real in-app browser journeys below**, driven interactively by the
   implementing agent in its in-app browser (the Codex in-app browser, or
   the equivalent the implementer has) against the same real Worker and
   database — a human-shaped pass through the actual UI, not a script.
3. The lower-level automated race, expiry-boundary, idempotency, migration
   and grant-writer parity tests already listed.

The fix is complete only when all three pass. Rules for every in-app
journey:

- **No mocking** of invitation, verification, sign-in or session endpoints
  (`page.route` fulfilments on `/api/v1/**` are forbidden in these specs; a
  wiring test asserts it).
- **No injected privilege**: no manually created sessions, no seeded grants
  or memberships for the recipient, no direct database writes to advance an
  invitation. The only seed is the owner's bootstrap organization. The
  recipient identity starts as an email absent from `users`.
- **Isolated owner and recipient authentication** — two separate browser
  profiles/contexts with separate cookie jars (in the in-app browser: two
  tabs are *not* isolation; use a second profile or a full sign-out with
  cookie clearing between roles, and record which was used).
- **Evidence retained per journey**: screenshots of each decisive state, the
  actual network evidence with status codes (the in-app browser's network
  reader, or the Playwright request log, filtered to `/api/v1/**` and
  `/auth/**`), the captured email bodies from Mailpit, and database
  assertions read through the harness's D1 access after each step (row
  counts and status values for `organization_admin_invitations`,
  `authentication_challenges`, `users`, `resource_access_grants`,
  `organization_memberships`, `user_roles`, `sessions`,
  `communication_messages`). **Redact before retaining:** email bodies with
  the invitation and verification tokens masked (keep the link shape and the
  message id), network captures with `Cookie`/`Set-Cookie` headers,
  `x-csrf-token`, passwords and any `token` body field masked, and screenshots
  taken with the password field empty or masked. Raw tokens, passwords and
  session cookies are never written to the evidence directory; a wiring
  check greps the evidence for the token pattern before it is kept.
- Time-dependent journeys (expiry, 15-minute boundary) use the harness's
  controlled clock, never a real wait, and assert the boundary from both
  sides.
- Note for the harness: the existing local dispatch helper
  (`dispatch-local`) is event-keyed; organization-scoped messages carry
  `event_id NULL` and reach Mailpit only through the queue consumer. A
  harness without the queue binding cannot run this gate and must say so
  rather than substituting a mock.

**Primary journey (the defect's own scenario), in order:**

1. Owner context: sign in, open Organization settings for an organization
   with **zero events**, invite a new email. Assert: pending row, one
   `queued`→`delivered` invitation message in Mailpit, no `users` row for the
   recipient, response body contains no token.
2. Recipient context: open the **actual link from the captured email**.
   Assert: page shows org name, Admin-not-owner scope and expiry; zero new
   rows anywhere; no session cookie.
3. Request the verification link from the page (click it twice — assert
   exactly one challenge row and one verification message exist); open the
   **second captured email**; press the confirm button (the non-consuming
   POST). For a new email the page now shows the registration form — assert
   `consumed_at_ms IS NULL`, no `users` row, invitation still `pending`. For
   the existing-account variant it shows the final confirm button, also
   without consuming.
4. Establish identity — one of three variants, with different assertions:
   - **New email:** submit registration. Assert, from one batch: challenge
     consumed, `users` row + credential, session.
   - **Existing account via the verification link:** press the final
     confirm. Assert: challenge consumed, session for the existing user id,
     no new `users` row.
   - **Existing account via own sign-in** (password or ordinary magic link
     in a fresh tab, ignoring the verification email): assert a session for
     the existing user id and that the verification challenge is **still
     unconsumed** — ordinary sign-in never touches it; it is invalidated
     later by acceptance (step 6).
   In all variants: zero grants/memberships/roles; the invitation page and
   the Account page both render without redirect loops (navigate to
   `/account`, `/admin`, and back to the invitation fragment URL; record
   each final URL).
5. Before accepting: from the recipient context request the org's admin
   pages and `GET /api/v1/admin/organizations/{org}/access-grants`. Assert:
   404s, and the shell's no-workspace state.
6. Click **Accept invitation**. Assert: one `manage` grant, membership row,
   organizer role, `authorization_version` bumped, invitation `accepted` with
   `accepted_by_user_id`, audit row, no remaining unconsumed challenge — in
   the own-sign-in variant this is the step that consumes the untouched
   verification challenge from step 4.
7. Assert access: the intended organization's settings and events pages
   load; a second, unrelated organization (seeded for the owner only) is 404
   for the recipient; sign out, sign in again with the recipient's own
   credential, and re-assert both.

**Additional in-app journeys**, each with the same evidence and prohibitions:
wrong-account sign-in on the link (no accept action, account-switch
guidance, nothing granted); resend (old link reads "no longer valid", new
link works, `expires_at_ms` unchanged in the database); expiry at the 72-hour
boundary and at the 15-minute verification boundary (controlled clock);
revocation before acceptance (old link reads revoked; verification and accept
refused); inviter-authority loss and Reissue (admin A invites, owner revokes
A, recipient is blocked with the explanation, list shows *Needs reissue*,
plain re-invite is refused as duplicate, owner reissues, recipient accepts on
the new link); delivery failure (dispatcher forced to fail via the local
provider configuration, list shows Failed with an allowlisted reason,
invitation still Pending, resend recovers); and the existing-account-without-
password-or-live-context variant of the primary journey.

A journey that cannot run is reported by name with the reason, never
replaced by a mocked equivalent or by the Playwright spec of the same name.
Add to §8's race tests: reissue with a forced failure between steps (3) and
(4) leaves the old row `revoked` with `superseded_by_id` NULL only if the
batch is not atomic — the test asserts the batch is atomic (old row still
`pending`, no replacement); verification request repeated while a live
challenge exists → one challenge row, one message row; **two verification
requests interleaved past the pre-read** (monkeypatched) → the loser's
guard aborts before its message insert, the handler re-reads and returns
202, exactly one challenge row and one message row, no 5xx; registration
POST with a consumed or expired challenge → 404 and no `users` row;
registration POST with a valid challenge whose invitation was revoked
meanwhile → 404 and no `users` row; **registration atomicity** — a forced
failure after the challenge compare-and-set (e.g. the user insert made to
violate a constraint) leaves the challenge unconsumed and no `users`,
`password_credentials` or `sessions` row, proving consumption and
provisioning share one batch rather than the consume-then-restore pattern of
`_redeem_magic_link`.

The table below is the checklist those journeys must satisfy (real local
Worker, D1, Mailpit; invitation APIs never mocked; screenshots and assertions
on the decisive states):

| Test | Observable result |
|---|---|
| Org with zero events invites a new email | Pending row; captured email; no user, grant or session created by creation |
| Open invitation after 20 minutes | Page still opens; verification can still be requested |
| Redeem verification after 15 minutes | Rejected; no grant |
| Day 2: new recipient verifies, registers, then accepts | After registration: account and session exist, no organization access, org requests denied; after the separate Accept: one `manage` grant, lands in the org workspace with onboarding |
| Existing account accepts | Same identity reused; no duplicate user |
| Existing account without password or live context | Ordinary magic link sends nothing; invitation verification signs them in (assert: session exists, `/auth/session` shows no organization access); before acceptance every protected request for the inviting org is denied (404) and the shell renders the no-workspace state without looping; after acceptance only the intended organization is accessible — an unrelated org and any previously revoked grant elsewhere remain denied |
| Wrong account opens link | Cannot accept; account-switch guidance |
| Resend | Old link shows "no longer valid"; new link works; expiry shown unchanged |
| Day 4 (invitation past 72 h) | Acceptance rejected as expired; resend refused; **Invite again** creates a new invitation with a fresh 3-day window that works |
| Inviter loses authority (A invites, owner revokes A, recipient submits) | Nothing granted; page says a current admin must send a new invitation; the list shows *Needs reissue*; a plain re-invite by the owner is refused as duplicate; owner's **Reissue** succeeds, the old link now reads revoked, the new link is accepted |
| Revoke before acceptance | Old link resolves to "revoked"; verification and acceptance refused; nothing granted |
| Duplicate clicks / retries (create, verification request, registration, accept) | **One invitation row, ultimately `accepted`**; one user; one grant; exactly **one invitation email and one verification email** captured |
| Email delivery fails (dispatcher forced to fail) | Delivery shows Failed with an allowlisted reason; invitation still Pending; resend recovers |
| Refresh / logout / login | Pending state and granted access persist |
| Cross-tenant | Read/resend/revoke/accept of another org's invitation fail without leakage |

Plus desktop and mobile Axe, keyboard, focus and reduced-motion checks on
Organization settings, the invitation page, the verify/registration page and
the Account page; regenerated `embedded_assets.py` and `openapi/openapi.json`;
existing event-invitation, speaker and reviewer suites unchanged and green;
`scripts/release_gate.sh` complete. No release-ready claim without the full
gate; blocked or skipped checks reported by name.

## 9. Documentation changes on implementation

`org-collaborator-gap.md` → `implemented`, pointing here; `product-status.md`
capability line; `api-security.md` note that invitation and verification
credentials are delivered by email only and that the invitation token is not
an authentication credential. This document moves to `approved` when the
decisions in §1 are accepted and to `implemented` when §8 is green.
