# Sessionbuddy debugging runbook

This is the common diagnostic path for API, page, and asynchronous failures. Use synthetic or redacted data; never enable ad hoc production request-body logging.

## First response

1. Record the environment, deployment version, route/page/job template, UTC time window, user-visible symptom, and incident owner.
2. Check release health for a matching deployment change, error-rate shift, latency shift, Python import/startup failure, or missing telemetry.
3. Obtain a safe request/correlation ID from the response, browser telemetry, or operator console. Never ask a user to send cookies, magic links, signed URLs, or private form content.
4. Decide whether impact is ongoing. Roll back progressively when an active deployment breaches its gate and rollback is schema-compatible.

## API regression

1. Compare route p50/p75/p95/p99, throughput, errors, response bytes, and sample count with the accepted baseline.
2. Use `Server-Timing` and the correlated trace to separate authentication, authorization, validation, D1, domain, and serialization time.
3. Inspect D1 statement count, duration, rows read, and `EXPLAIN QUERY PLAN` against the same seed. Check for missing tenant-leading indexes, scans, N+1 queries, or changed page size.
4. Check cache state, Python cold/import time, dependency-lock change, compatibility date, and response serialization size.
5. Reproduce with the registered benchmark and matching dataset/concurrency/cold-warm conditions.

## Page regression

1. Compare LCP, INP, CLS, TTFB, transition duration, bundle bytes, browser family, device profile, navigation type, and sample count.
2. Identify critical-path API requests and compare browser timing with their `Server-Timing` breakdown.
3. Inspect asset size/caching, request waterfalls, hydration/long tasks, layout shifts, and loading/error-state behavior.
4. Reproduce in the registered desktop and mid-tier mobile Playwright journey with the same seed.

## Async delay or failure

1. Follow correlation and deterministic idempotency keys from domain commit to outbox, Queue, Workflow, and provider callback.
2. Inspect oldest-message age, attempts, safe error code, dead-letter state, Workflow lateness, and provider status.
3. Confirm the domain mutation committed before replaying an effect. Use only the authorized, audited replay action; never edit queue/outbox state manually.

## Availability

1. Check `/health`, `/api/v1/health`, Python startup/import telemetry, Worker errors, and D1 availability separately.
2. Distinguish missing telemetry from a successful zero-error state.
3. Confirm configuration/binding names and migration version for the affected environment without printing secret values.

## Closing an incident

Add or strengthen a regression test and benchmark, verify the fix in preview/staging, record before/after results, deploy progressively, and confirm recovery in production. Material incidents record root cause, detection gap, corrective action, owner, and follow-up deadline.
