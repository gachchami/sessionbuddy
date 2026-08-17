# Event management design

> **Status:** IMPLEMENTED DESIGN — NOT IMPLEMENTATION AUTHORIZATION.
>
> **Purpose:** Preserve the event-management product decisions, interaction
> contract, safety boundaries, and verification expectations.
> **Authority:** Current `AGENTS.md`, code, schema, API contracts, and executable
> tests outrank this design. Verify current behavior before proposing work.

### Design refinements
1. **Open removed from the ledger**; the event name is the single workspace entry, Settings and Duplicate are visibly secondary (§2).
2. **Lifecycle/publication truth table** added (§7a); all lifecycle copy and tests derive from it. User-facing term stays **Active**.
3. **Branding purge**: per-organization iteration on the existing `idx_event_branding_assets_pending` index with `EXPLAIN QUERY PLAN` evidence, no new migration; pending assets served `Cache-Control: private, no-store` instead of the one-year immutable header (§4).
4. **Recent changes**: one canonical DOM outline with CSS-grid placement, plus the test list (§2).
5. **Access-loss (403) state** and Create/Duplicate button order pinned (§5, §6).

### Product decisions
- No Active → Draft in the UI. The API transition stays for compatibility and is not exposed until an explicit pause/unpublish workflow coordinates event visibility, CFP publication, public schedule publication, and organizer confirmation.
- Activate may save pending edits and activate in one PATCH; Archive and Restore require a clean form.
- Activation button state is explicit: clean form → **Activate event**; dirty form → **Save and activate**.
- Activation copy states eligibility, not publication: the event becomes *eligible* to publish its call for proposals and schedule separately.
- The 24-hour scheduled branding purge is the release requirement and the authoritative cleanup (covers tab closure, crashes, expired sessions, abandoned uploads). No DELETE endpoint is required for this consolidation.
- **Follow-up, not a release blocker:** immediate deletion of a pending asset on Discard/replacement.

## 0. Decisions closed in v3

| Critique item | Decision | Grounding |
|---|---|---|
| P0 branding assets | **Purge in this release; accept public-by-unguessable-URL.** Add `purge_pending_branding_assets` to the existing `scheduled` handler (`src/entry.py:36`, cron `*/2 * * * *`) alongside the CFP staged-asset and speaker-upload purges; no API surface change. Pending rows with `event_id IS NULL` older than 24 h are deleted with their R2 object. Asset URLs are UUIDv4 (`new_id()`), and logos/covers are public content once saved, so pre-save exposure by URL is by design and documented. | `entry.py:80–99`, `access.py:3040–3060`, `0001_baseline.sql:565–576` |
| Home's dominant job | **Event switcher first, compact secondary activity.** The ledger is the page; counts link into work; activity is a ≤ 8-item "Recent changes" rail for organization managers. No attention queue on Home. | §2 |
| Lifecycle | **Draft → Activate · Active → Archive · Archived → Restore (as draft / and activate).** No Active → Draft in the UI: the public CFP query requires `e.status='active'` (`cfp/router.py:1033`), so demotion would silently unpublish. The API still accepts it; the UI does not offer it. | §7 |
| P1 Create/Duplicate | Fully specified as editor modes with the same state coverage as Settings. | §5 |
| P1 Lifecycle state machine | Explicit table: authority, confirmation, consequences, dirty-form rule, progress/failure/success, focus and destination. | §7 |
| P2 More menu | **Removed.** Duplicate is a visible tertiary action on the row. | §2 |
| P2 activity reachability | Activity precedes the pagination control in DOM order and is reachable from a "Recent changes" skip target. | §2 |
| Constraints list | All adopted; see §12. | — |

Carried from v2 unchanged: routed three-mode editor, `list_events` scope correction (backend, same model), three-way stale reconciliation, uploads dirty-until-saved, archived-editing/Restore separation, `/admin/events` 302 alias, manifest handling, `SessionBuddyAccess`.

---

## 1. Information architecture and routes

**Organization level `/admin`**: organization name (`h1`) · organization selector (only with > 1 manageable organization) · **Create event** · event ledger · Recent changes rail (organization managers) · quiet Organization settings link.

**Event level `/admin/events/{id}`**: Overview · CFP · Proposals · Rounds · Reviewers (gated on `canAdministerAccess`) · Speakers · Messages · Agenda · Share · **Settings** (last, visually separated as a utility).

**Routed editor** — one document `event_editor.html`, one script `event_editor.js`:

| URL | Mode | Reads | Writes |
|---|---|---|---|
| `/admin/events/new?organization_id=` | create | session, organizations | `POST /api/v1/admin/organizations/{org}/events` (+ `Idempotency-Key`) |
| `/admin/events/new?source={id}` | duplicate | session, `GET /api/v1/admin/events/{source}` | `POST /api/v1/admin/events/{source}/duplicate` |
| `/admin/events/{id}/settings` | edit | session, `GET /api/v1/admin/events/{id}` | `PATCH /api/v1/admin/events/{id}` |

| Route | Change |
|---|---|
| `GET /admin` | unchanged route; new content |
| `GET /admin/events` | 302 → `/admin` (query forwarded, `Cache-Control: no-store`); no persona check on the alias |
| `GET /admin/events/new` | new; `require_document_persona(ORGANIZER)`; **registered before** `/admin/events/{event_id}` (Starlette matches in registration order; otherwise `require_document_event("new")` → indistinguishable 404) |
| `GET /admin/events/{event_id}/settings` | new; persona + `require_document_event(..., include_archived=True)`; failure indistinguishable from missing event |
| `GET /admin/event-editor/assets/event-editor.js` | new, `content_addressed_asset` |
| `GET /admin/home/assets/home.js` | handler switched to `content_addressed_asset` (the route change is required, not just the HTML reference) |
| `GET /admin/events/assets/events.js` | **removed together with `events_admin.js` and its `ASSETS` entry.** Rationale: the only document that referenced it is `events_admin.html`, which is served `no-store`, so no cached document can request it after deploy. |

`observability/manifest.json`: add `/admin/events/new` and `/admin/events/{event_id}/settings` to `pages`; move `/admin/events` to `excluded_routes` ("Compatibility alias: 302 to /admin; renders no measurable document."). Each new script sets `window.__sessionbuddyTelemetryDraft.page_template` to its manifest path (`/admin`, `/admin/events/new`, `/admin/events/{event_id}/settings`) as `admin_submissions.js:893` does.

Shell (`app_shell.js`): `eventIdFromLocation()` returns `""` for the literal segment `new`; `pageLabel()` adds `Settings` and `New event`; `eventNav()` appends Settings; `SessionBuddyAccess` exported (§8).

---

## 2. `/admin` — event switcher first

### Header
`h1` organization name. Right: organization `<select>` (hidden with one; `?organization_id=` honoured; `session.organization_id` default; on change: reload ledger, move focus to the ledger heading, announce "{n} events for {organization}"), **Create event** (`canManageOrganization(session, selectedOrg)`), quiet **Organization settings**.

### Ledger (primary)
Toolbar: All · Active · Drafts · Past (`aria-pressed`), sort, search (debounced, 100-char cap). `role=table` with `rowgroup/row/columnheader/cell`; empty state outside the table (`aria-live`); **Load more** with `isStaleCursor` reset; `view/q/order/organization_id` mirrored via `history.replaceState`.

Row (one `role=row`; the event name is the only link to the workspace; no whole-row anchor):

| Cell | Content |
|---|---|
| Event | name (link → Overview) · Draft / Archived chip · attention line, rendered **only when non-zero** and **as links**: "3 proposals" → `/admin/events/{id}/submissions`; "2 awaiting review" → `/admin/events/{id}/submissions#rounds-title` |
| Date | localized range in the event time zone (`<time datetime>`) |
| Where | location, or "Online" for virtual |
| Program | CFP state · schedule state (text chips) |
| Actions | **Settings** · **Duplicate** (secondary utilities, quiet styling; Duplicate rendered only when `canDuplicateEvent`) |

The event name is the only workspace entry (no separate Open). A row therefore has at most four tab stops: name, up to two attention links, Settings, Duplicate. No overflow menu. No Archive/Restore here (§7). Mobile: each row becomes a card; `role=row/cell` are preserved on the card and its cells (CSS reflow only; the header row is visually hidden with the `.sr-only` clip pattern, never `display:none`, so column headers stay associated); action buttons ≥ 44 px.

### Recent changes (secondary)
Mounted only when `canManageOrganization(session, selectedOrg)` (`GET …/activities` requires `RESOURCE_ACCESS_MANAGE`, `access.py:4976`); otherwise not in the DOM.

**One DOM outline, placed by CSS grid — the rail is never duplicated or moved:**
```
main#main.organizer-main
  header.page-heading            (h1, selector, Create event, Organization settings)
  p#status[role=status]
  div.organizer-home-grid        (display:grid)
    section#events[aria-labelledby=events-title]      grid-area: events
      toolbar · div[role=table] · p#event-list-empty
    aside#recent-changes[aria-labelledby=recent-title] grid-area: changes   ← DOM position: after the table, before pagination
    div.organizer-home-pagination                        grid-area: more    (Load more)
```
Grid: ≥ 60rem `grid-template-areas: "events changes" "more changes"` (rail beside, sticky top); < 60rem `"events" "changes" "more"`. Reading and tab order are the DOM order in every layout. Skip links: "Skip to events", "Skip to recent changes" (targets `#events`, `#recent-changes`, both `tabindex="-1"`).
Tests: DOM order (`events` → `recent-changes` → `pagination`) at both widths; skip-link focus lands on `#recent-changes`; rail absent from the DOM for a non-manager session; 200 % zoom (viewport 640 px effective) uses the single-column layout with no horizontal scroll; after card reflow every `role=cell` still resolves its `columnheader` (Axe `aria-required-children` + a query that maps cells to headers).
- Up to 8 entries after dropping `operation === "read"`. Because the endpoint returns 30 rows, unpaginated, filtering may hide older mutations; the heading is **Recent changes** and the empty copy is **"No recent changes."** (not "No activity yet"). Failure: "Recent changes are temporarily unavailable." + request id.
- Entry: `{actor_name} {created|updated|removed} {resource label}{ subject_name}` — never `resource_id` — with a relative time whose `<time datetime="…" title="…">` carries the absolute timestamp for AT and hover.
- Formatter: extract `activityResource`/`activityVerb`/sentence builder from `organization_admin.js` into a shared `activity_format.js` (content-addressed, used by both pages) with a static test pinning the label map; do not duplicate.
- "See all changes" → `/admin/organization#organization-activity`.

No metric tiles.

---

## 3. Backend correction: event-list scope

`list_events` (`access.py:2056`) admits organization owners/managers via `has_organization_access` and then restricts rows to `owned.owner_user_id=? OR grant_access.permission IN ('edit','manage')` (`:2107`), contradicting `policy.py:70–75` and documented in `docs/org-collaborator-gap.md:136`.

Fix: when `has_organization_access` is true, the row condition is `e.organization_id=?` only; otherwise keep the ownership/grant condition. `_has_event_access_in_organization` remains the gate for event-only actors. `view`, `q`, `order`, cursor unchanged. Foreign organizations still 404. `EventList`/`EventView` unchanged; `openapi/openapi.json` unchanged. Tests: org manager sees events created by others; event-only editor sees granted events only; foreign org 404; page 2 keeps scope. Update `org-collaborator-gap.md`. Sequenced first (§14).

---

## 4. Branding asset purge (backend, this release)

`purge_pending_branding_assets(db, bucket, now_ms, limit=200)` in `platform/auth/branding_purge.py` (or beside the speaker purge).

**Query shape — must use the existing index.** The only index is `idx_event_branding_assets_pending (organization_id, status, created_at_ms, id)` (`0001_baseline.sql:1472`); a global `WHERE status='pending' AND event_id IS NULL AND created_at_ms <= ?` scans the table. The purge therefore iterates organizations (`SELECT id FROM organizations`) and per organization runs `SELECT id, object_key FROM event_branding_assets WHERE organization_id=? AND status='pending' AND created_at_ms <= ? AND event_id IS NULL ORDER BY created_at_ms, id LIMIT ?`, which is a range on the index's leading columns. No new migration. The test asserts `EXPLAIN QUERY PLAN` for that statement reports `USING INDEX idx_event_branding_assets_pending` (no `SCAN`), per the AGENTS.md query-plan rule. If organization counts ever make the loop the bottleneck, the recorded alternative is an incremental migration adding a partial index `(created_at_ms) WHERE status='pending' AND event_id IS NULL`.

Per row: delete the R2 object (`_event_logo_bucket`), then the row; count `deleted_rows/deleted_objects/delete_failures`; log a structured `event_branding_pending_purge` line like `speaker_pending_upload_purge` (`entry.py:86`). Wire into `scheduled` after the speaker purge, sharing its `limit`.

**Cache treatment.** `GET /api/v1/public/event-assets/{name}` currently returns `Cache-Control: public, max-age=31536000, immutable` for every status (`access.py:3118`), so a purge cannot retract cached copies. Change the header by status (no schema change): `pending` → `private, no-store`; `attached` and `retired` → unchanged one-year immutable (their URLs are content-addressed by asset id and never reused). Residual, accepted and documented: a pending image fetched by the organizer's own browser before Save may sit in that browser's cache until eviction; nothing else can have cached it.

Tests: fresh-and-upgrade DB, twice-run no-op, a pending asset newer than 24 h survives, attached/retired rows untouched, the header matrix by status, and the query plan. Documented in `docs/product-status.md` with the accepted exposure: pending assets are reachable only by their UUIDv4 URL, uncached, until saved or purged.

---

## 5. Editor modes — complete specifications

Shared by all modes: single form; sections General → Date and time → Branding → Email → (Lifecycle in edit only) → sticky save actions; `beforeunload` when dirty; validation per §6; upload behaviour per §6; permission read-only fallback on 403 (§8); telemetry `page_template`.

### 5.1 Create — `/admin/events/new`
- **Organization**: from `?organization_id=`; if absent, `session.organization_id`; if the account manages several, a selector at the top of General (changing it clears nothing; it only sets the POST target). If the account manages none, the page renders "This account does not administer an organization." and no form (the shell already routes event-only editors away from `/admin`).
- **Guided minimum**: General and Date and time are open; Branding and Email are collapsed `details` labelled "Optional" (all model-required fields are in the open sections: name, format, location, description, time zone, start, end — `EventCreate` requires them all). Time zone is prefilled from the browser with "Detected: {tz}" help.
- **Actions** (pinned order, left → right): **Save draft** (secondary) · **Create active event** (primary, rightmost, `Enter` default). Lede per §11. Activation validation: end > now. Rationale: a fresh event is usually configured to go live; draft is the escape hatch.
- **Idempotency**: `Idempotency-Key` per body fingerprint (existing logic). A retry after a network failure replays; a 409 from the create path shows "Event creation is still being processed. Try again to safely check the same request." and re-enables the button.
- **Progress**: buttons disabled, status "Creating event…" / "Saving draft…" in the save bar `aria-live`.
- **Success**: navigate to `/admin/events/{new_id}` (Overview) with one-time status "Event created." / "Draft saved." (`sessionStorage` handoff, as other pages do). Focus lands on the Overview `h1`.
- **Failure**: 422 → field errors; 403 → "You need organization management access to create events."; 401 → draft preserved to sessionStorage + sign-in redirect (existing).

### 5.2 Duplicate — `/admin/events/new?source={id}`
- **Source context** banner above General: "Duplicating {source name} · {date range}" with a link back to the source Overview.
- **Prefill** (`GET /api/v1/admin/events/{source}` — archived sources allowed; `duplicate_event` does not exclude archived): name = "{source} copy" (server makes it collision-safe: "… copy 2"), dates/time zone/format/location/description/accent/website/email identity copied; logo/cover shown with "Will be copied to the new event" and `retain_source_logo/cover=true` unless the user uploads a replacement.
- **Copied / not copied** list shown in the banner's disclosure ("What is copied?"): copied — details, dates, time zone, location, description, branding, email identity; **not** — CFP, slug, proposals, reviews, reviewers, speakers, onboarding, schedule, messages, access grants, audit history (matches `duplicate_event` and the checkpoint).
- **Source states**: `GET` 404/403 → "That event is no longer available to duplicate." with a link to `/admin`; the form is not rendered. Source changed since load → `duplicate` 409 → "The source event changed. Reload to duplicate the latest version." with **Reload**.
- **Actions** (pinned order, left → right): **Create active event** (secondary) · **Save draft** (primary, rightmost, `Enter` default). The emphasis is deliberately the reverse of Create: a copy inherits the source's dates, location, and description, which almost always need editing before it should appear in public listings, and the server also defaults `EventDuplicateCreate.status` to `draft`. `status` in body; `source_version` from the fetched event.
- **Success**: navigate to `/admin/events/{new_id}/settings` with "Draft saved. You are now editing the copy." (the copy usually needs date changes first). Focus on the Settings `h1`.

### 5.3 Settings — `/admin/events/{id}/settings`
Layout, save model, lifecycle per §6–§7. Loading: skeleton `h1`, disabled form, "Loading event…" in the status region; on `GET` failure use `redirectIfWorkspaceUnavailable` / `redirectIfDocumentAccessChanged` (as `event_overview.js:37`) else inline error with request id.

---

## 6. Save, validation, uploads, stale recovery

**Layout**: main column (General, Date and time, Branding) + supporting column (Email, Lifecycle) ≥ 60rem; single column below; section ids `#general #date-time #branding #email #lifecycle`; form width ≈ 46rem; sticky save bar inside the content frame with `scroll-padding-bottom` so focused fields clear it; safe-area insets on mobile.

**Snapshots**: baseline / local / latest. Dirty = per-field `local ≠ baseline`. Save enabled only when dirty and valid.

**Ordinary save**: complete `EventUpdate` + `version` + `status` (current unless a Lifecycle action set it) → replace baseline/version from the response → "Event saved." → focus status region.

**Validation** (inline, `aria-invalid`, error text under the control): end ≤ start; invalid time zone; insecure website URL; activation with past end ("Update the event dates before activating." — focus Ends); server 422 via `showValidationErrors`; pending unselected upload blocks Save ("Upload the selected image or clear it before saving.").

**Uploads** (verified): `POST /api/v1/admin/organizations/{org}/event-assets/{logo|cover}` → `{asset_url, kind}`; the event version is unchanged; attachment happens by DB trigger on the next successful create/PATCH. Editor: store URL in the field, **mark dirty**, keep version, show "Upload complete · Save changes to use this image." Discard restores the baseline URL; a second upload supersedes the first; a failed PATCH keeps the URL for retry; 422 `invalid event {kind} asset` → "Upload the image again." Orphans are removed by §4.

**Access loss (403 on any mutation, or `redirectIfDocumentAccessChanged` on reload)**: the form switches to read-only in place — every control gets `readonly`/`disabled`, local values stay visible and selectable (copyable), Save/Discard and all Lifecycle buttons are disabled, the upload buttons are disabled and any uploaded-but-unsaved URL is shown with "Not saved — you no longer have access to save this event" (the pending asset is left for the purge). A banner "Your access to this event changed. Your unsaved edits are shown below but cannot be saved. Ask an organization administrator to restore your access." receives focus. `beforeunload` is **removed** at that point (nothing can be saved, so the prompt would be noise); the sessionStorage draft is written once so the values survive a reload for copying. The page does not redirect away.

**Stale save (409)**: keep local → `GET` latest → per field: unchanged locally → take latest; changed locally only → keep; changed both → conflict. Conflict panel lists only conflicts ("Event name changed elsewhere" · latest value · **Keep mine** / **Use latest**); Save disabled until resolved; baseline := latest. Never dump JSON or replace the whole form. Focus moves to the panel heading.

---

## 7. Lifecycle state machine (Settings only)

| From → To | Trigger | Authority (UI ↔ server) | Confirmation | Consequences shown | Dirty form | Progress / failure | Success: message · focus · destination |
|---|---|---|---|---|---|---|---|
| draft → active | **Activate event** (clean form) / **Save and activate** (dirty form) | `canEditEvent` ↔ `EVENT_MANAGE` | none (non-destructive); inline validation end > now | "The event becomes eligible to publish its call for proposals and schedule. Each is published separately." | Allowed: with a dirty form the button relabels to **Save and activate** and sends all edits with `status:"active"` in the same PATCH | Button busy "Activating…" / "Saving and activating…"; 4xx per §6; 409 → §6 | "Event activated." · status region · stay |
| active → archived | **Archive event** (danger) | `canManageLifecycle` ↔ `RESOURCE_ACCESS_MANAGE` | Modal: "Archive {name}?" — copy from §7a (Archive). Confirm: **Archive event** | as in the modal | **Blocked** while dirty: "Save your changes before archiving." | Modal confirm busy; failure inside the modal; 409 → close modal, §6 | "Event archived." · archived banner · stay (page now in archived mode) |
| archived → draft | **Restore as draft** | `canManageLifecycle` ↔ `RESOURCE_ACCESS_MANAGE` | none | copy from §7a (Restore as draft) | Blocked while dirty (same copy) | busy; failure inline | "Event restored as a draft." · status region · stay |
| archived → active | **Restore and activate** | same | none; validation end > now, focus Ends on failure | copy from §7a (Restore and activate) | Blocked while dirty | busy; failure inline | "Event restored." · status region · stay |
| any → duplicate | **Duplicate event** | `canDuplicateEvent` ↔ `ORGANIZATION_MANAGE` + `EVENT_MANAGE` read | none | "Creates a separate draft with the same setup; proposals, people, and schedule are not copied." | If dirty: `beforeunload` prompt (navigation) | — | navigates to `/admin/events/new?source={id}` |
| active → draft | — | not offered in the UI (see §0) | — | — | — | — | — |

Archived events: General/Date and time/Branding/Email stay editable; ordinary Save keeps `status:"archived"`; an ordinary edit can never restore.

### 7a. Lifecycle × publication truth table (single source for copy and tests)

Verified rule: every public surface gates on the event row — public events list (`competition/router.py` `WHERE e.status='active'`), public CFP (`cfp/router.py:1033` `f.status='published' AND e.status='active'`), public schedule (`scheduling/router.py:2664` `status='active'`). Stored CFP status and the published schedule revision are **never changed** by an event status change. Effective visibility = **event active AND surface published**.

| Event status | Stored CFP | Stored schedule | Public events list | Public CFP | Public schedule | Organizer can publish CFP / schedule? |
|---|---|---|---|---|---|---|
| draft | not set / draft | none / draft | hidden | hidden | hidden | no — publish is rejected ("Activate the event before publishing its CFP.") |
| draft | published (from an earlier active period) | published | hidden | hidden | hidden | no |
| active | not set / draft | none / draft | listed | hidden | hidden | yes |
| active | published | published | listed | **public** | **public** | yes |
| archived | published | published | hidden | hidden | hidden | no (CFP workspace not exposed for archived events) |
| archived | not set | none | hidden | hidden | hidden | no |

Derived copy (the only sentences allowed to describe consequences):
- Activate: "The event becomes eligible to publish its call for proposals and schedule. Each is published separately." (row 3)
- Archive: "It disappears from active and public listings. A published call for proposals and public schedule go offline, and stay published for when the event is restored. Proposals, agenda, and speaker records are retained." (rows 5–6)
- Restore as draft: "The event returns as a private draft. Anything published stays offline until you activate it." (row 2)
- Restore and activate: "The event returns to active listings. A published call for proposals and public schedule become public again." (row 4)
- Ledger Program cell shows the stored state ("CFP published", "Schedule published") and, when the event is not active, appends "· offline" so stored and effective states are both visible.

Tests derive from this table: for each row, one Python test asserting the three public endpoints' visibility for that (event status, CFP status, schedule status) combination, and one static test asserting the copy strings above exist verbatim in `event_editor.js` and `admin_home.js`.

---

## 8. Permissions (advisory in UI, enforced by API)

| Authority | View Settings | Edit / Activate | Duplicate | Archive / Restore |
|---|---|---|---|---|
| Event `edit` | yes | yes | no | no |
| Event `manage` / owner | yes | yes | only with org owner/manage | yes |
| Organization owner / manage | yes | yes | yes | yes |
| Others | indistinguishable 404 | — | — | — |

`SessionBuddyAccess` (exported from `app_shell.js`) — every helper takes an **exact organization or event**:
- `canManageOrganization(session, organizationId)` — org `owner|manage` for that id (the existing shell helper answers "any organization" and is not reused for this).
- `canEditEvent(session, event)` — event `owner|edit|manage` or org `owner|manage` for `event.organization_id`.
- `canManageLifecycle(session, event)` — event `owner|manage` or org `owner|manage`.
- `canDuplicateEvent(session, event)` — `canManageOrganization(session, event.organization_id)`.
Presentation only; every mutation handles 403 ("Your access changed while this form was open…") and switches the form to read-only.

---

## 9. Navigation and compatibility

Home Create → `/admin/events/new?organization_id=`; ledger/nav Settings → `/admin/events/{id}/settings`; ledger/Lifecycle Duplicate → `/admin/events/new?source={id}`; CFP builder "Change in event settings" → `/admin/events/{id}/settings#date-time` (set by `admin_programs.js`); Engine Room → `/admin`; `/admin/events` → 302 `/admin`; on `/admin`, `#event-form` is translated once to `location.replace("/admin/events/new" + org query)`; no other legacy fragments honoured. Runbook step 2 and README updated.

---

## 10. Typography, responsive, accessibility

System UI stack; no public-page serif; labels above controls; **type scale is enforced by `tests/release_readiness/test_typography_scale.py` across every stylesheet**: font sizes only from {12, 14, 16, 18, 22, 28, 36, 48} px (rem × 16), nothing below 12 px, weights only 400/500/600/700/800, and any `input`/`textarea`/`select` rule ≥ 16 px (iOS zoom guard; file inputs exempt). Pick page title 28, section title 18 (22 if the supporting column needs a peer), body 16, help and chips 14, nothing at 12 except dense table meta.

**Time-zone disclosure is enforced by `tests/release_readiness/test_admin_datetime_context.py`** (pattern from CFP/agenda/rounds/tasks): each date/time input group references a `{scope}-time-zone-context` element via `aria-describedby`, the context contains a `{scope}-time-zone` element whose text is the event's `time_zone`, and all input↔epoch conversions use that zone (`timeZone: state.eventTimeZone`), never `new Date(value)`. The editor's Date and time section adopts this with ids `event-time-zone-context` / `event-time-zone`; the ledger's Date column renders `<time datetime>` formatted in each event's own zone. shared grid tracks; help text aligned to its control; no eyebrows, nested cards, or filler prose. Wide/intermediate/mobile behaviour per v2 §9 plus: ledger cards keep table semantics; activity precedes pagination; 44 px targets; save bar `scroll-padding-bottom` + safe-area; 200 % zoom follows the mobile structure; reduced motion disables sticky-bar and panel transitions. Focus rules: organization switch → ledger heading; Create/Duplicate success → destination `h1`; Save → status region; 409 → conflict panel heading; Archive → archived banner. Axe (wcag2a/aa/21a/21aa) desktop + 390×844 on `/admin`, `/admin/events/new`, `/admin/events/{id}/settings`.

---

## 11. Copy

| Where | Text |
|---|---|
| Ledger actions | Settings · Duplicate (event name is the workspace link) |
| Ledger Program cell | CFP published · Schedule published — with "· offline" appended when the event is not active |
| Access lost | Your access to this event changed. Your unsaved edits are shown below but cannot be saved. Ask an organization administrator to restore your access. |
| Attention line | {n} proposals · {m} awaiting review (links) |
| Create lede | Drafts stay private. An active event is eligible to publish its call for proposals and schedule; each is published separately. |
| Activate button | Activate event (clean) · Save and activate (dirty) |
| Activate consequence | The event becomes eligible to publish its call for proposals and schedule. Each is published separately. |
| Settings lede | Name, dates, location, and branding apply to public event pages when saved. The call for proposals and the agenda publish separately. |
| Save bar | Unsaved changes · All changes saved · Discard · Save changes |
| Upload | Upload complete · Save changes to use this image. |
| Stale panel | Someone else saved this event while you were editing. Decide the fields you both changed. — Keep mine / Use latest |
| Archive modal | Archive {name}? — §7a Archive copy |
| Dirty block | Save your changes before archiving. / … before restoring. |
| Duplicate banner | Duplicating {name} · {dates} — What is copied? |
| Toasts | Event created. / Draft saved. / Event saved. / Event activated. / Event archived. / Event restored. / Event restored as a draft. |
| Recent changes | {actor} {created/updated/removed} {resource} {subject} · {relative} — No recent changes. / Recent changes are temporarily unavailable. |

---

## 12. Implementation constraints (from the critique, all adopted)

1. `list_events` scope fixed before `/admin` is canonical (§3).
2. Permission helpers take an exact organization/event id (§8).
3. Legacy `events.js`: route, file, and `ASSETS` entry all removed in the same change (§1).
4. `admin_home_javascript` route switched to `content_addressed_asset` together with the HTML reference.
5. Activity formatter extracted to a shared, tested module (§2).
6. "No recent changes." copy; read filtering acknowledged as lossy (§2).
7. `page_template` telemetry in every new script (§1).
8. Each content-addressed HTML contains exactly one reference per registered script (`_versioned_html` raises otherwise): `admin_home.html` → `home.js`, `activity_format.js`; `event_editor.html` → `event-editor.js`.
9. Relative times carry absolute `datetime`/`title` (§2).
10. Table-to-card CSS keeps `role=row/cell` (§2).

---

## 13. Tests and docs

Rewrite: `test_admin_home_event_cards.py`, `test_admin_home_event_creation.py`, `test_events_management_theme.py`, `test_event_table_accessibility.py`, `test_organization_settings_placement.py`, `test_destructive_archive_ui.py` (event part), `test_app_shell_navigation.py` (keep "no global Events link"; add Settings nav, `pageLabel`, `eventIdFromLocation("new")`, `SessionBuddyAccess`), `test_static_wiring.py`, `test_observability.py` (manifest).
Add (Python): `list_events` scope (§3); branding purge (§4); `/admin/events` 302; `/admin/events/new` 200 for organizer persona; `/admin/events/{id}/settings` 200 owner/edit-grantee/org manager/archived, 404 outsider.
Add (Playwright): `admin-home-responsive.spec.ts` (ledger, visible actions, activity placement and skip link, no overflow); `event-editor.spec.ts` (create draft/active + idempotent retry; duplicate prefill + copied-list + source 404; settings edit/save/version; 409 with one conflict; upload-then-save; archive modal + dirty block; restore-and-activate rejection focuses Ends; `beforeunload`); `accessibility.spec.ts` new paths; `cfp-builder-clean-state.spec.ts:201`; `error_handling.spec.ts:56` / `accessibility.spec.ts:106` stop visiting `/admin/events`.
Docs: `product-design-checkpoint.md`, `external-evaluation-runbook.md` step 2, `README.md`, `org-collaborator-gap.md`, `product-status.md` (capability + accepted branding exposure).
Completion standard per `AGENTS.md`; `git diff -- openapi/` empty; embedded assets regenerated once.

---

## 14. Decisions to reverse only deliberately

1. Branding: purge in this release; public-by-UUID-URL before save accepted and documented.
2. Home: switcher first; counts are links; compact Recent changes rail for organization managers.
3. Lifecycle UI: Activate / Archive / Restore only; no Active → Draft.
4. Ledger actions visible (Settings · Duplicate; event name is the entry); no overflow menu, no separate Open.
4a. Purge iterates organizations on the existing index (no migration); pending assets served `no-store`.
4b. All lifecycle/publication copy derives from the §7a truth table.
5. Archive and Restore require a clean form; Activate saves with the edits and relabels to "Save and activate" when dirty. (Approved.)
5a. Immediate pending-asset deletion on Discard/replacement is a follow-up; the 24-hour purge is authoritative. (Approved.)
6. Create lands on Overview; Duplicate lands on the copy's Settings.
7. Legacy `events.js` route and file removed outright.
8. `/admin/events` is a 302 alias in `excluded_routes`; `#event-form` translated client-side only.
