# Scheduling agenda acceptance record

Status: application complete and included in the deployed Cloudflare development Worker

Scheduling implements the conflict-safe agenda slice from `requirements.md` Section 5.5:

- Accepted sessions, rooms, exclusive/non-exclusive tracks, revisioned agenda items,
  and explicit speaker joins are tenant-scoped in D1.
- Database triggers reject event-bound, room, speaker, and exclusive-track conflicts
  at write time. Preview checks exist for fast feedback but are never authoritative.
- Saves use optimistic versions and atomic D1 batches; stale moves return `409` and
  the UI visibly restores the last server state.
- Published revisions are immutable. Publishing atomically promotes the reviewed
  draft and clones a separate next draft, so later edits cannot alter the published
  schedule.
- Publication records audit and durable calendar-sync intent. Stable per-recipient
  UIDs, monotonic RFC 5545 sequence numbers, immutable ICS versions, private email
  messages, and deterministic delivery keys make delivery retry-safe.
- Admin list/day/week/track/room views support drag-and-drop and an equivalent
  keyboard form. Authenticated staff and speakers get a responsive read-only view.

## Local review

Start the containerized Worker and apply local migrations:

```bash
docker compose up --build --detach worker
docker compose run --rm --no-deps worker npm run worker:migrate
```

Complete first-run setup, sign in as an administrator, create an event, and accept
at least one proposal. Then open:

- `http://localhost:8787/admin/events/{event-id}/agenda`
- `http://localhost:8787/events/{event-id}/schedule`

The admin page never manufactures agenda content. Empty events remain empty until
an administrator accepts and schedules real sessions.

## Automated evidence

```text
Host suite:              245 passed
Container suite:         245 passed
Scheduling Workerd smoke:    passed
Ruff and diff check:     passed
```

The scheduling tests cover conflict preview, atomic creation, stale-version
rejection, publication, calendar queue planning, and authenticated read-only
schedule retrieval using isolated fixtures.

Provider delivery remains an activation task: configure a verified sender and
provider secret before enabling real outbound email. The public schedule embed and
automatic schedule optimization remain P2 roadmap items.
