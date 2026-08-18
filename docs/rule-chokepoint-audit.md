# Rule and chokepoint audit

**Purpose:** Define the mechanical audit that detects when a product rule gains
an unregistered writer, a duplicate source of truth, an impossible browser
fixture, a misleading source-only test, or an expired exception.

**Lifecycle status:** implemented

**Authority:** This document defines audit policy. `rule_audit.json` registers
the current canonical rules and writers. `scripts/audit_rule_chokepoints.py`
derives the current findings from the tree and is the executable authority for
release-gate results.

## Operating contract

The release gate fails only on mechanical findings tied to a registered rule:

- a production writer appears outside a registered writer inventory;
- a registered writer disappears;
- a designated canonical constant is duplicated outside an allowed projection;
- a registered persisted enum member never participates in a policy comparison;
- a session mock emits an `event_access` field absent from OpenAPI;
- a source-membership test has a name that implies behavioral coverage;
- a temporary duplication or waiver is incomplete or outlives its removal
  trigger.

Literal-vocabulary clustering is advisory. Overlapping values discover possible
rules across naming and capability boundaries, but overlap alone never proves
that two implementations share semantics and never fails the release gate.

## Calibration

Each detector ships with an executable fixture reproducing the defect shape it
was designed to catch. A detector is promoted to the mechanical gate only when
that fixture demonstrates a precise result without unrelated findings. New
escaped defects extend the calibration corpus rather than being recorded in a
chronological review log.

For each escaped defect, the review asks:

1. What mechanical signal could have found it?
2. Can that signal remain precise enough to gate?
3. Was it already visible in an output that nobody was required to read?

## Exceptions

Temporary duplication and waivers are declarations, not indefinite ignore
lists. Each declaration must identify its owner, user path, removal mechanism,
and a machine-checkable triggering event. When that event fires while the
exception remains, the audit reports a mechanical P1 finding.

The generated finding matrix is not checked in. Run the audit for current
state:

```sh
uv run python scripts/audit_rule_chokepoints.py --json
```

Use `--include-advisory` to include literal-vocabulary clusters for manual
triage.
