# Release readiness release-hardening acceptance record

Status: local release gate, isolated Cloudflare development activation,
organization-only first-administrator bootstrap, authenticated browser rehearsal,
and direct-R2 upload complete

Release readiness turns the product capabilities into a repeatable, container-first release candidate.

## Security and privacy

- Test-only behaviors default to `production` when `APP_ENV` is
  missing. A missing binding cannot enable privileged identities or local
  uploads, scanner bypass behavior, or local communication dispatch.
- The existing permission matrix, event/organization substitution, evaluator
  assignment, speaker ownership, signed session, CSRF/origin, upload quarantine,
  download-grant, and per-recipient calendar boundaries are covered by the
  release suite.
- Security-sensitive denials remain non-disclosing and use the shared error envelope.

## Accessibility and browser compatibility

- Core journeys have skip/focus targets, visible focus, 44px controls,
  programmatic error associations, live status/receipt announcements, table
  headers, reduced-motion handling, and responsive states.
- The agenda retains a keyboard form equivalent to drag-and-drop.
- Containerized Playwright/Axe runs every core shell at desktop and Pixel 7 widths.
  The accepted run passed all 18 checks with no serious or critical violations.

## Data, migration, and recovery

- The deterministic release seed creates 10,000 submissions, 2,000 speakers,
  50,000 tasks, and 2,000 agenda items in bounded batches.
- Query-plan tests cover the submission, speaker, task-dashboard, and agenda list
  paths and reject temporary ORDER BY plans.
- The isolated backup/restore rehearsal applies migrations from empty storage,
  checks integrity and foreign keys, compares schema hashes and row counts, and
  completed the full envelope in approximately 4.6 seconds on this workstation.

## Local performance evidence

These are diagnostic container measurements, not a Cloudflare staging baseline:

| Journey | Sample | p50 | p75 | p95 | p99 | Errors |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Foundation status API | 100, concurrency 4 | 25.1 ms | 28.4 ms | 46.0 ms | 71.6 ms | 0% |
| Agenda read API | 100, concurrency 4 | 83.0 ms | 96.1 ms | 126.3 ms | 135.2 ms | 0% |

Containerized Lighthouse mobile simulation:

| Page | Performance | Accessibility | FCP | LCP | TBT | CLS |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Foundation | 100 | 100 | 943 ms | 1,103 ms | 0 ms | 0 |
| Agenda shell | 100 | 100 | 933 ms | 933 ms | 0 ms | 0 |

The dedicated Chrome DevTools MCP trace integration was unavailable, so Lighthouse
and Playwright provide the checked-in workflow for this local gate. Artifacts live
under ignored `.local/benchmarks` and `harness/.local/lighthouse` directories.

## Reproduce

```bash
./scripts/release_gate.sh
```

The gate builds the Worker and frontend, applies local migrations, runs lint and
the full test suite, executes the large seed and restore rehearsal, runs all capability
smokes, desktop/mobile browser accessibility checks, API benchmarks, Lighthouse,
and a Wrangler deployment dry run. It does not deploy or mutate remote resources.

After an authorized development deployment, run the read-only remote audit:

```bash
docker compose run --rm --no-deps worker \
  uv run python scripts/cloudflare_preflight.py --config "$MAIN_CONFIG"
```

The accepted 2026-08-09 rehearsal reported 23 passing deployment checks and zero
pending or failed checks. D1 had no pending migrations; the R2 bucket and strict
CORS policy, Queues/DLQs, producer/consumer triggers, Workflow, HMAC and provider
secrets, health route, browser routes, and anonymous `401` identity boundary were
present. No Cloudflare Container was configured.

The one-time bootstrap created `SessionBuddy Development` and its administrator
without manufacturing an event. The administrator can create the first real event
from the application when its details are known. Resend accepted the live magic-link
message through the communications queue after the deployed Python queue-handler
contract was verified.

The authenticated development rehearsal then created and edited an event, accepted
and revoked invitations, accepted an existing-user speaker role, published a
conditional form, restored and submitted a draft, verified speaker ownership, and
uploaded a 1,732-byte PNG directly to R2. D1 records the asset as clean/current
under the explicit development scan bypass. Deployed Worker version
`82c8daf7-9cc5-4c17-8324-0cabde887a25` receives 100% of traffic. Final verification
passed 286 Python tests, 28 desktop/mobile browser checks, lint, embedded-asset
consistency, and all 23 Cloudflare activation checks.

## Remaining promotion gates

Before production promotion:

1. Verify a client-controlled sender domain before sending to arbitrary recipients;
   Resend's shared test sender is limited to the account address.
2. Development scanning is explicitly bypassed. A production promotion requires
   a reachable scanner endpoint and secret; it must never inherit this bypass.
3. Run the full Section 8 concurrency envelope and compare it with an accepted
   staging baseline; local SQLite/query-plan evidence is not a substitute for D1.
4. Exercise provider retry/dead-letter recovery and record the D1 restore operator,
   recovery point, recovery time, and application smoke result.
5. Record the client-approved retention/consent policy and actual traffic/data
   envelope listed in `requirements.md` Section 14.
