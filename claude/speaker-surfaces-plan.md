# Speaker surfaces design

> **Status:** IMPLEMENTED DESIGN — NOT IMPLEMENTATION AUTHORIZATION.
>
> **Purpose:** Preserve the audience, truthfulness, performance, accessibility,
> and anti-goal decisions for public and authenticated speaker surfaces.
> **Authority:** Current `AGENTS.md`, code, schema, API contracts, and executable
> tests outrank this design. Verify current routes and tests before treating any
> file-level step as work still required.

## 1. Job and audience

Public attendees need to recognise the event, choose the right way to browse its
speakers, understand who a person is, and reach that person's sessions quickly.
Speakers using `/speaker` need the same event identity inside a multi-event
workspace without replacing the authenticated account shell.

The mode is **Experience** on the public list/gallery and **Operate** in the
speaker portal. Public identity may be expressive; the portal stays compact and
task-led.

## 2. Required outcomes

1. CFP, Schedule, Speaker list, and Speaker gallery look and behave like pages
   from one event, including when no logo or cover has been uploaded.
2. The scored List and Gallery widgets remain separate routes and remain
   surname-ordered. Name, job title, and company stay identical across both.
3. List and Gallery serve visibly different browsing jobs rather than exposing
   the same cards at different column counts.
4. Biography expansion behaves identically in the public dialog and standalone
   public person profile.
5. Each event section in the multi-event speaker portal carries compact event
   identity without turning the portal into a stack of public-page banners.

## 3. Shared public event masthead

Add `static/public_event_masthead.js`, exported as
`window.SessionBuddyPublicEventMasthead`. Each public document contains one empty
`[data-public-event-masthead]` mount before its surface-specific content. The
helper owns the DOM for:

- event logo, or a two-character monogram fallback;
- cover image, or an accent-derived fallback field;
- event name as a compact, non-heading brand anchor;
- stable primary public navigation: Schedule and Speakers;
- `aria-current="page"` and meaningful image alternatives.

The surface title — Schedule, Speakers, or Call for Proposals — is the document's
sole `h1`. The masthead does not accept arbitrary facts and does not silently
link the event name to an organizer-supplied external website. A separately
labelled **Event website** link may appear in surface content when available.

The helper accepts a normalized object rather than knowing API response shapes:

```text
render(mount, {
  event: { id, name, accentColor, logoUrl, coverUrl, websiteUrl },
  active: "schedule" | "speakers" | null,
  speakersView: "list" | "gallery" | null,
  speakersQuery: string,
  embedded: boolean
})
```

Callers normalize existing responses. Shared identity does not mean invented or
identical facts. CFP keeps its authoritative dates, location, deadline, and
application state in its page intro. Schedule keeps its time zone and revision-
derived publication state below the masthead. Speakers has no publication-state
signal and therefore uses the truthful empty copy **No speakers have been
announced yet.** CFP intentionally has no masthead nav item because Schedule and
Speakers responses do not expose its slug. The component is suppressed entirely
when `embedded` is true.

The visual is a compact identity band, not a second hero: a 5rem-high desktop
brand field and 4rem-high mobile field, a 3rem/2.5rem logo or monogram, one-line
event name with safe wrapping for long names, and a fixed navigation row below.
Cover imagery is a quiet cropped background with a contrast scrim; the accent
fallback occupies the same geometry. The whole masthead, including navigation,
must stay below 9rem on desktop and 8rem on mobile before content wrapping.

The new script is a content-addressed packaged asset referenced by
`public_cfp.html`, `schedule.html`, and `speaker_gallery.html`. Shared styles live
in `product.css` under `.public-event-masthead`; page-specific styles may only
control the space after it. The design uses the existing scoped
`--event-accent` derivation for `.cfp-public-page`, `.schedule-page`, and
`.speaker-gallery-page`.

Packaging is explicit: add `PUBLIC_EVENT_MASTHEAD_JS` and
`BIOGRAPHY_DISCLOSURE_JS` to `ASSETS`, expose stable routes, add three masthead
and two biography consumer tuples to `CONTENT_ADDRESSED_ASSETS`, and load scripts
in API client → helper → page order. Route/static-wiring and packaged-asset checks
must cover both helpers. A `schedule.css` edit receives its own version bump.

## 4. Speaker list versus gallery

Both routes use the same response, surname sorter, search, dialog, and facts.
They differ in information density and reading pattern:

### `/events/{id}/speakers` — Speaker list

- one-column result list capped at 68rem;
- compact square portrait, name, job title, company, three-line biography
  preview, and up to two sessions;
- session titles are direct schedule links;
- action label: **View profile**;
- optimised for scanning names, organisations, topics, and session relevance.

### `/events/{id}/gallery` — Speaker gallery

- portrait-led four/two/one-column grid;
- 4:5 portrait, name, job title, company, and one featured session;
- no biography on the card;
- action label: **View profile**;
- optimised for visual browsing.

The primary masthead exposes one **Speakers** destination. Inside that surface,
a labelled List / Gallery view switch uses ordinary links with `aria-current`.
The current `q` query is preserved when switching routes. Empty results hide the
search control when the collection itself is empty; a filtered zero state keeps
the search and says no speakers match.

The Gallery's featured session is deterministic: the earliest session by
`starts_at_ms`, then normalized title, then session id. If no start time is
available, normalized title then id decides. Both views retain headshot, name,
job title, and company to satisfy the public-widget contract.

## 5. Shared biography disclosure

Add `static/biography_disclosure.js`, exported as
`window.SessionBuddyBiographyDisclosure`. It owns only expansion semantics:

- `aria-controls` and `aria-expanded`;
- **Show more** / **Show less** text;
- `.is-collapsed` state;
- post-layout overflow measurement and hiding the control when content fits.
- remeasurement after `document.fonts.ready` and container-width changes.

The dialog and public profile remain responsible for mounting their static or
dynamic biography nodes. This removes duplicated behavior without coupling the
two page renderers. The asset is content-addressed in both documents.

## 6. Event identity inside `/speaker`

Extend `SpeakerEventView` with optional `accent_color` and `logo_url`, both
defaulting to `None`. Add those existing event columns to `_speaker_row` and the
event-list query and both model construction sites; there is no migration.
Regenerate both OpenAPI artifacts.

Each `.event-group__head` renders:

- a compact 3rem logo or monogram;
- event name, date range, and time zone;
- a thin accent-derived top treatment;
- no cover image.

The event identity remains inside the event section. It never replaces the auth
shell or global portal greeting, and it must remain legible for speakers with
one hundred event memberships. Missing/invalid images fall back to the monogram.
Large membership sets start as compact event summaries with explicit expansion;
only the active/expanded event renders its operational detail. Existing batched
event fetching must not gain additional per-event branding queries or image
requests.

## 7. States and accessibility

- No branding assets: accent field + monogram; never an empty image box.
- Image failure: hide the failed image and restore the fallback.
- Empty speaker collection: masthead remains, search is hidden, and the truthful
  neutral state says **No speakers have been announced yet.**
- Filtered zero: search remains and status reports zero matches.
- Dialog: labelled, bounded, Escape-closeable, contained scroll, focus returned
  to opener, reduced motion respected.
- 320px, 390px, desktop, 200% zoom, keyboard, Axe, and embed checks are required.
- All literal font sizes and clamp endpoints introduced here use the enforced
  scale. Focused tests assert new clamp endpoints; the separate global parser
  debt is not folded into this release.

## 8. Boundaries and anti-goals

- Do not merge or remove List or Gallery; both are scored public widgets.
- Do not add given/family-name fields, a migration, or server-derived sort key.
- Do not redesign the organizer Speaker directory or its Summary/Edit split.
- Do not reverse the tested horizontally scrollable event navigation on mobile.
- Do not put the full public masthead inside the authenticated speaker portal.
- Do not fabricate event imagery, speaker facts, or publication status.

## 9. Verification contract

- Existing EMB criteria: surname order; Show more; visibly distinct grid; same
  name/title/company across List and Gallery.
- New browser coverage: shared masthead anatomy and fallbacks across CFP,
  Schedule, List, and Gallery; image-error recovery; list/gallery semantic
  differentiation and query preservation; true/filtered empty states; embeds;
  portal multi-event
  branding; 320/390 desktop/mobile Axe and focus behavior.
- Python: additive `SpeakerEventView` fields, tenant-safe cross-organization
  portal query tests, and a 100-event response without extra branding queries.
- Regenerate `openapi/openapi.json` and embedded assets; bump shared product and
  affected page-script identities once; update `docs/product-status.md`.

## 10. Out of scope

- Structured given/family-name data and server-side surname ordering.
- Global typography-test parsing for `clamp()` endpoints and normalization of
  existing off-scale debt.
