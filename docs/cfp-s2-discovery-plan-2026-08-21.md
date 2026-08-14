# CFP-S2 discovery — implementation plan

**2026-08-21. Diagnosed and planned; not implemented.**

CFP-S2 is the scenario that starves the downstream scenario areas on a fresh
instance: without a submitted proposal there is no review input, no accepted
speaker, no schedulable session, and no published program. This document is the
agreed plan, the decisions behind it, and the traps an implementer will hit.

## The defect

A speaker cannot reach a call for proposals for an event they are not already
associated with.

The only surface listing published calls is the landing page's
`[data-public-events]` grid (`static/app_shell.js:786-812`), fed by
`GET /api/v1/public/events` (`competition/router.py:48-72`), which already
returns `cfp_slug` and already builds a correct `/cfp/{event_key}/{slug}` link.
That surface is invisible to authenticated users: the root handler
303-redirects every session to its persona home (`api/app.py:121-149`).

The speaker portal is membership-scoped by deliberate product decision
(`docs/product-status.md:293-297`). `_open_call_view`
(`speaker_operations/router.py:838-899`) computes the submit control only for
the *selected* event, and the portal's event list is joined through
`event_memberships` (`speaker_operations/router.py:665-687`). A call on an event
the speaker does not belong to cannot appear.

### The empty state is the wrong target

Both routes to the speaker persona also create an `event_speakers` row:
accepting a speaker invitation (`platform/auth/access.py:5575-5589`, via
`_add_speaker_profile`) and submitting through the public CFP
(`cfp/router.py:2490-2545`). Without one of them the user has no speaker role and
`require_document_persona` (`platform/auth/http.py:145-160`) 403s `/speaker`
before any portal UI runs. So a genuinely empty portal is a narrow edge case —
a revoked membership or an archived speaker row.

The real CFP-S2 shape is: **the speaker is provisioned for event A, CFP-S1
publishes a call on event B, and the populated portal has no route to it.**
`#empty-state` never renders. Any fix that lives only in the empty state does
not close the scenario.

## What is already true — do not rebuild it

| Fact | Anchor |
| --- | --- |
| `/api/v1/public/events` returns the canonical CFP link inputs | `competition/router.py:48-72` |
| `/speaker` serves its shell without membership; only the data endpoint 404s | `speaker_operations/router.py:229-232` |
| The portal already routes that 404 to `#empty-state` | `static/speaker_portal.js:1520-1527` |
| Submission provisions the whole graph in one batch | `cfp/router.py:2490-2545` |
| The shell already supports guest-tolerant pages via `data-allow-guest` | `static/app_shell.js:823-835`, used by `speaker_portal.html` |
| Availability has exactly three canonical states | `cfp/availability.py` |

Consequence: this is a navigation and contract problem, not a data,
schema, or permissions problem. No migration is required.

## Decisions and rationale

These took the longest to settle and should not be silently reversed.

**The primary fix belongs in the populated portal.** Cross-event discovery must
be a persistent section of the normal `/speaker` workspace, not a treatment of
the zero-proposal empty state. See "The empty state is the wrong target" above.

**`/api/v1/public/events` stays an event directory.** It is not a CFP
directory. The renderer attaches `Schedule →` on `schedule_published` and
`Speakers →` on `speaker_count` independently of `cfp_slug`
(`static/app_shell.js:797-812`). Filtering the endpoint down to CFP-bearing
events before its `LIMIT 100` would silently delete schedule and speaker
discovery for every event whose call has closed. The CFP-bearing selection gets
its own query, ordering, and limit.

**Per-user capacity never becomes an availability state.** `_form_availability`
(`cfp/router.py:3051-3058`) already accepts `submissions_received` and
explicitly discards it, noting that the configured limit is per authenticated
speaker and enforced by `create_submission`. Keep `cfp_state` a pure function of
`(opens_at_ms, closes_at_ms, now_ms)`. Exhaustion belongs to `actionable` /
`remaining_submissions`. A fourth state makes the anonymous and personalized
contracts structurally different and rebuilds the drift this plan removes.

**The speaker endpoint deliberately skips the resource cascade.** Every existing
`/api/v1/speaker/*` route runs `require_permission(..., ResourceContext(org,
event, owner))`. Cross-event discovery has nothing to check — org B grants the
caller nothing, so running the cascade would deny exactly the intended results.
The endpoint rests on public CFP data plus the caller's own aggregates. **This
must be explained in the route docstring** or it will later be "fixed" into
denying cross-event discovery.

**Three renderers, one shared state helper.** Landing renders event cards with
all states labelled; `/calls` renders call cards and hides closed; the portal
section renders call cards plus personalized fields. The layouts and filtering
policies legitimately differ. What must be shared is the
`cfp_state` + boundary → label/copy mapping, including date formatting, so
"Opens", "Closes", and "Closed" cannot disagree between surfaces.

**The roleless fix is adjacent platform hardening, not CFP-S2.** Tracked and
tested separately; outside CFP-S2 acceptance scoring.

## Implementation sequence

1. **Shared CFP selection and temporal availability.** Server-side helpers for
   selecting the canonical published form for an event and computing
   `scheduled | open | closed` plus boundary timestamp and kind. One canonical
   form-ordering rule used by `/api/v1/public/events`,
   `/api/v1/speaker/open-calls`, `_open_call_view`, and `/calls`. Today the
   public endpoint orders by `published_at_ms DESC, id DESC` while
   `_open_call_view` orders by `version DESC` — these can select different rows
   for the same event. Do not preserve that disagreement.

2. **Preserve event-directory semantics.** `GET /api/v1/public/events` keeps its
   existing event selection, ordering, and `LIMIT 100`, and continues returning
   events discoverable by schedule or speakers with no CFP. Add only
   non-personalized CFP state and boundary fields. No caller-specific data.

3. **Public calls directory.** Persona-neutral `GET /calls` with
   `open_calls.html` / `open_calls.js`, its own CFP-bearing selection,
   deterministic ordering, and a limit applied *after* identifying events with a
   canonical published form. Shows scheduled and open; hides closed. Serves
   anonymous visitors, shared links, and roleless accounts. It does not by
   itself close CFP-S2.

4. **Personalized speaker endpoint.** `GET /api/v1/speaker/open-calls`, speaker
   persona required, no resource grants applied, not scoped to the portal's
   selected event. Shared CFP fields plus orthogonal `submission_count`,
   `remaining_submissions`, `already_submitted`, `actionable`. Two queries
   total: one for candidate calls, one grouped `GROUP BY form_id` aggregate for
   the caller's submitted counts, joined in Python. No per-call query.

5. **Mount discovery independently in the portal.** The Calls for Proposals
   section must be a **sibling of both `#portal` and `#empty-state`**, loaded
   independently, so it survives whether the membership-scoped request succeeds
   or 404s. Shows scheduled and open, hides closed, disables or explains
   temporally-open-but-exhausted calls, and always works outside the selected
   event. Proposal creation stays on `/cfp/{event_key}/{slug}`. The empty state
   references this section; it does not contain a second implementation.

6. **Reliable speaker navigation.** "Calls for proposals" → `/speaker#calls`.
   The section renders after async loads, so the browser resolves the hash
   against a DOM that does not yet contain it: after render, detect the hash,
   `scrollIntoView`, and move focus for keyboard and screen-reader users. The
   browser regression asserts the section is *reached*, not that the URL
   contains the hash.

7. **Roleless root navigation, separately.** `_session_home_destination`
   (`api/app.py:110-118`) raises 403 when no persona home resolves. Route that
   user to `/calls` or another persona-neutral start. Note the client-side twin:
   `renderShell` paints `renderSessionContractError()` when `activeRole` is null
   (`static/app_shell.js:566-571, 852-866`) — fixing only the server yields a
   page that loads and then declares the account broken.

8. **Preserve submission provisioning.** Do not modify the submission
   transaction unless integration forces it. `selection_status='submitted'` is
   authoritative.

9. **Regenerate every derived artifact.** `scripts/embed_console_assets.py`, and
   `scripts/generate_openapi.py` — which writes **two** outputs,
   `openapi/openapi.json` and `src/sessionbuddy/api/openapi_contract.py`
   (embedded `OPENAPI_JSON` bytes). Verify both changed together.

10. **Regression coverage.** Below.

## Coverage

**Server.** Canonical form selection agrees across all three consumers; multiple
published versions cannot produce different links; scheduled/open/closed
boundaries match submission enforcement exactly (closure is inclusive at
`closes_at_ms`); per-user capacity never changes `cfp_state`; public event
selection and its 100-event limit are unchanged; calls-directory ordering and
limit operate only on CFP-bearing events; the speaker endpoint executes exactly
one grouped submissions aggregate regardless of candidate count; event B appears
for a speaker associated only with event A; the public response carries no
personalized fields; unpublished forms disappear.

The query-count assertion needs no new machinery — several suites already
hand-roll a recording D1 fake with `prepare(sql)`
(`tests/cfp/test_cfp.py:130`, `tests/evaluation/test_evaluation.py:61`,
`tests/security/test_d1_authorization_facts.py:25`). Record the SQL strings and
assert one aggregate against `submissions`.

**CFP-S2 browser regression.** Provision the speaker for event A; publish an
open CFP for event B; open the *populated* portal; follow persistent calls
navigation; confirm async hash scrolling reaches the section; discover event B
and open its CFP; submit; confirm event B and the proposal appear in the portal;
confirm the proposal remains `submitted`.

**Edge case.** A membership-scoped portal 404 does not hide the independently
mounted calls section.

**Separate platform test.** A roleless authenticated account visiting `/`
reaches `/calls` without a 403.

## Traps

- **`accepted_at_ms` does not mean accepted.** Both insert paths
  (`cfp/router.py:2540`, `platform/auth/access.py:5901`) set it to `now`
  alongside `selection_status='submitted'`. An assertion phrased against that
  column passes when it should fail. Test `selection_status`, and say why in the
  test comment.
- **Shell route classification.** `currentSection()`
  (`static/app_shell.js:480-488`) returns `""` for `/calls`, which makes
  `organizerWorkspace` true and bounces an organizer via `location.replace` at
  line 596. Return `"calls"` and add it to the persona-neutral allowlist
  alongside `account`, `speaker`, `reviews`. For anonymous access, put
  `data-allow-guest` on the shell element as `speaker_portal.html` does.
- **Multi-role nav mismatch (pre-existing, out of scope).**
  `static/app_shell.js:669` renders a "Speaker portal" link whenever
  `session.account_roles` contains `speaker`, but `/speaker` requires
  `active_persona === speaker`. Confirm persona switching handles this, or
  record it as a separate defect. Do not let the new calls link inherit the
  pattern, and do not expand CFP-S2 scope to fix it.
- **Do not make `/speaker` a discovery surface.** Its membership scope is
  deliberate (`docs/product-status.md:293-297`). The new section is additive and
  sits beside the scoped portal data.

## Accepted limitations

- `/calls` intentionally hides closed calls, so a bookmarked directory entry
  disappears once its call closes. Canonical `/cfp/{event_key}/{slug}` links
  remain stable and render the authoritative closed state.

## Release criterion

A speaker provisioned for one event can discover and submit to a scheduled or
open CFP for another event from the populated speaker workspace, without an
out-of-band URL; all consumers agree on the canonical form and its temporal
availability state.

Separately, and outside CFP-S2 scoring: an authenticated roleless account has a
non-error route to public CFP discovery.
