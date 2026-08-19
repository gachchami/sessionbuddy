# SessionBuddy UAT

Purpose: execute every imported evaluation scenario and rubric, with explicit
SessionBuddy identity, dependency and acceptance requirements.

Lifecycle: implemented test definitions and validation tooling; creating this
pack does not execute product UAT.

Authority: repository security and product contracts outrank this pack.
`overlay.json` owns SessionBuddy execution corrections. `specs/`, `fixtures/` and
`manifest.json` are a permanently credential-sanitized derivative of kit revision
`81099583ff4c878f310dc3c48e4916318678425d`, not current execution authority.
`derivation.json` records upstream and packaged hashes plus exact redaction
locations. Four fixture password values became JSON null and two inline attendee
passwords became `[RUNTIME_ONLY]`. No original credential values or plaintext
backup are retained. All other source bytes, steps and feature assertions remain
unchanged; the sanitized files must not be described as byte-identical upstream.
`execution.json`, `bindings.template.json` and `results.template.json` are generated
from checked-in inputs by `scripts/eval_uat.py`; do not edit them by hand.

## Coverage and independence

All seven areas, 20 original scenarios, 201 original steps, 98 rubrics, success
signals, manual instructions and five fixture assets are preserved. CFP-S1 is
split into A (steps 1–2), B (3–8), C (9–12), without dropping or repeating steps.
This is a repository-owned split, not a claim that the archived kit included it.
The original CFP-S1 success signals apply to the complete source scenario after
S1C, not independently to S1A or S1B. S1A hands off the recorded canonical event;
S1B hands off the configured published call and public URL; S1C adds the public
validation evidence and separate secondary-event identity.
Three additional cases cover time-zone preview/update, canonical track-ID embed
filters failing closed, and scorecard type switching. Every auto-partial criterion
has distinct automatic/manual verdicts; fully manual and conditional manual
instructions also have dedicated result records.

This is a human/browser-agent UAT procedure with an offline compiler and validator,
not a Playwright implementation. No external kit checkout, provider or network is
needed to build or validate it. Running UAT naturally requires an authorized target
and test accounts. Working credentials are deliberately not bundled. Permanent
null / `[RUNTIME_ONLY]` placeholders are not usable passwords.

## Runtime credentials stay separate

The offline compiler does not load credentials, sign in, or run browser actions.
The authorized human/browser executor resolves authentication only when a login
step requires it. Prefer the local app's demo sign-in controls when available.
Otherwise the executor may read the relevant account's credential from an
operator-provided ignored `.dev.vars` or another existing ignored credential
file, or a process environment variable such as `UAT_SPEAKER_PASSWORD`. Match the
account to the run's non-secret persona binding before filling the password UI.
Never infer that source aliases or null placeholders are working credentials.

The executor must keep the credential in memory for that login only, never print
it, put it in a command-line argument, capture it in screenshots, or copy it into
bindings, fixtures, execution plans, reports or Git. Do not read an entire secret
file into evidence. Existing local working credentials stay untouched. No loader
is implemented in `eval_uat.py`: it intentionally never reads credential files or
environment variables. Missing runtime access blocks the login step; it does not
justify a default password or modifying a tracked placeholder. Build/check and
materialize work without any credential source.

## Persona-to-account table

| Source name | Run binding | Rule |
|---|---|---|
| Jordan Alvarez | organizer | Select one provisioned organizer; do not create a second from a snapshot email. |
| Priya Raman / Sasha Speaker | speaker | One primary speaker, normally demo-speaker@sessionbuddy.demo. |
| Sam Whitfield | reviewer | One reviewer, normally demo-reviewer@sessionbuddy.demo. |
| Marcus Okafor | speaker2 | Distinct invitation contact; accept only when required. |
| Dana Kowalski (CSV) | import_contact | Distinct CRM contact, not organizer Dana. |
| Second reviewer | reviewer2 | Distinct account/browser context for isolation checks. |
| Alex Attendee | attendee | Anonymous first, otherwise a distinct attendee; never organizer credentials. |

Names/emails in source prose are aliases for this table, including searches,
assertions, invitations and recipients. Sanitized password placeholders are never
execution credentials. The materialized CSV uses these bindings and aligns primary speaker
metadata with sample data. Never feed the provenance CSV into a live run.
Contacts not accepted yet may have a null account ID and an evidenced role such
as `pending_invitation`. Actual login steps require verified account IDs. Passwords
stay in your credential manager, never the bindings or results.

The default reset does not provision `reviewer2` or `attendee`. Reserve distinct
controlled addresses and record `verified_role: reserved`, null account IDs, and
evidence of the reservation at preflight. Before reviewer-isolation checks, invite
and explicitly accept reviewer2 through supported UI in a separate context; then
record the observed account ID and role. Use anonymous attendee browsing unless
login is required; if needed, provision through supported signup before use.
Unprovisioned identities cannot execute steps. If these flows are unavailable,
record the affected checks as blocked, not passed or silently skipped.

## Prepare and execute

1. Select an authorized disposable UAT target. This pack grants no permission to
   reset databases, deploy, launch paid evals or email real recipients. Verify the
   origin, deployment, organization and provisioned login accounts. Record preflight
   evidence; do not assume old UUIDs or browser sessions remain valid.
2. Copy `bindings.template.json` to a run-local evidence location, then fill run
   metadata and persona bindings with observed identifiers. Resolve secondary
   contacts without prematurely accepting invitations. Event IDs start null if
   the target has no operational events.
3. Materialize an isolated packet into a new directory (overwrite is refused):

   ```sh
   docker compose run --rm --no-deps worker uv run python scripts/eval_uat.py \
     materialize /workspace/.local/uat/bindings.json \
     --output /workspace/.local/uat/run-unique
   ```

   `.local/uat/` is the designated ignored run-evidence location. The packet has
   resolved sample data, CSV, calendar and results. The headshot, slides and Europe
   data remain bundled under `fixtures/`; no external file download is required.
4. Follow `execution.json`: read global policy, each `before` precondition, the
   source step, then its `execution_correction`. Corrections supersede conflicting
   identity/date/navigation instructions; original feature assertions still apply.
   Execute in order. SPK-S2 must sign in as the existing speaker and explicitly
   accept the correct event invitation, never execute the source signup fallback.
5. Record organization, primary event and secondary event UUIDs and the public CFP
   URL as created. Navigate by those IDs, not event name. Never manufacture a
   replacement event to recover a missing fixture. The secondary-event probe must
   leave the primary binding unchanged. Each step records its actual actor and
   event, or an explicit reason why no event applies.
6. Preconditions and handoffs require observations and evidence. Dependencies
   require passing upstream handoffs, not guessed state. Missing fixtures block
   downstream work; do not patch them through SQL/API shortcuts. An optional
   unsupported feature can fail its rubric while a genuinely complete fixture
   handoff still passes. Record exactly which records make that handoff ready.
7. Assess all rubrics using their complete source criteria/evidence in the plan.
   Finish every manual sub-result. A banner does not prove email delivery, file
   contents, or cross-account isolation. `not_applicable` is allowed only for
   conditional manual follow-ups on auto rubrics, or for ABS-14 as a whole when
   evidence from UI and product claims establishes that AI review is not claimed.
   For ABS-14, overall, automatic and manual verdicts must all agree on the
   evidenced absence. Advertised-but-broken functionality fails; inaccessible
   evidence blocks. N/A cannot excuse an unavailable required feature.
8. Validate before handoff:

   ```sh
   docker compose run --rm --no-deps worker uv run python scripts/eval_uat.py \
     validate /workspace/.local/uat/run-unique/results.json --final
   ```

Keep pass/fail/blocked/pending distinct. Failures require a class: product, harness,
identity_fixture, provider, environment. Blocks require a cause. Capture safe
evidence references, not passwords, cookies, tokenized links or private asset URLs.
Redact sensitive captures. A structurally valid report is not necessarily a passing
product: inspect its recorded verdicts.
Credential screening checks keys and common secret-bearing prose/URL patterns,
but is heuristic: it cannot recognize every bare secret or inspect referenced
screenshots. Human redaction remains required. No command sends evidence to an
external service; the offline validator reads only the specified local packet.
For redacted evidence, write “Credentials removed from capture” and reference a
sanitized local file. Do not retain credential assignment syntax such as
`token: redacted` or token-bearing URLs: the conservative scanner rejects these
even when their values are placeholders.

## Dates and state

The materialized calendar uses America/Los_Angeles and ISO run_date. Initial review
runs D−1 through D+14, final review D+15 through D+30, CFP D−1 through D+30,
primary event D+45 through D+47, secondary event D+60 through D+61. Enter local
start/end-of-day times appropriate to each control and record saved timestamps.
Do not copy expired review-window literals from the snapshot. CFP-S4's deliberate
past close remains a negative test. ABS-S1 must explicitly reopen the same event's
call; incompatible immutable earlier decisions block the fixture, never authorize
silently changing final decisions.

Day 1/2/3 are D+45/46/47. Confirmation and biography tasks are due D+10, release
D+18, print headshot D+17, and slides D+34. Confirmation/profile deadlines allow
ten days for a manual run; the other deadlines preserve the source's lead times
before the conference. Before resuming a run after its earliest deadline, inspect
overdue tiles and reminder effects and record elapsed-time interference rather
than attributing it to the product. Do not silently re-anchor an in-progress run.
The packet rewrites communication and task fixture dates
as well as event dates. These calendar values supersede source date literals in
steps and success signals; the event's name remains a stable label.

SB-TZ-01 runs only after AIA-S2 and ABS-S2 hand off published agenda and deadlines.
Its bar is preserved agenda wall-clock times but unchanged deadline UTC instants,
explicit confirmation, atomic conflict rejection, audit evidence and stale-preview
rejection. SB-EMBED-01 checks all five outputs, unmatched JSON, speaker caching and
registry track labels. SB-SCORE-01 reproduces zero-weight-to-Free-text saving and
unsaved numeric draft restoration. These are acceptance requirements, not claims
that the current product already satisfies them. Missing controls must be recorded
as failures or blocked evidence as specified; never fabricate a successful check.

## Maintenance

```sh
docker compose run --rm --no-deps worker uv run python scripts/eval_uat.py build
docker compose run --rm --no-deps worker uv run python scripts/eval_uat.py check
docker compose run --rm --no-deps worker uv run pytest -q \
  tests/release_readiness/test_eval_uat_pack.py \
  tests/release_readiness/test_eval_uat_execution.py
```

Upstream hashes identify provenance; packaged hashes guard the sanitized derivative
and unchanged files. They cannot reconstruct or independently re-prove omitted
credential values. Separate execution tests prove source-step
traceability, identity/date resolution, exact result IDs, dependency gates and
required manual verdicts. Neither proves product behavior. Evolve the overlay
with a version bump and tests; never rewrite snapshot criteria to quiet failures.
