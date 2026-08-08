# Evaluation evaluation acceptance record

Status: application complete and included in the deployed Cloudflare development Worker

Evaluation implements the MVP submission-review lifecycle:

- Admins select submissions, configure the numeric range, recommendation
  choices, evaluator guidance, and one or more active event evaluators.
- `balanced` assignment distributes submissions deterministically round-robin;
  `all` assigns every selected evaluator to every selected submission.
- Evaluators see only active assignments connected to their authenticated user.
  They may save resumable drafts, finalize valid rubrics, or declare a conflict
  before finalization.
- Conflict declarations revoke the assignment atomically and are visible to
  admins. A submission left without an active evaluator must be reassigned
  before the round can close.
- Admin progress reports finalized/active counts per submission and evaluator.
- A submission's aggregate is the arithmetic mean of its individual finalized
  numeric ratings. The round aggregate is the arithmetic mean across all
  individual finalized ratings, weighted by each submission's finalized count.
  Values are rounded to two decimal places for display.
- Equal aggregate scores remain ties. SessionBuddy never breaks a tie or makes
  a decision automatically; an authorized human records the decision.
- Accept/reject uses a second confirmation step. Once recorded, the decision is
  immutable in both the UI and API and cannot be changed by a future product
  workflow. Recording it never sends communication.
- A round closes only when every selected submission has at least one active
  assignment and every active assignment is finalized. Closure makes evaluation
  content permanently read-only. The schema supports later sequential rounds,
  while the UI permits one open round per program.

All mutations use the shared opaque session, CSRF, RBAC, idempotency, audit,
tenant-scoped D1, error, and observability contracts established in platform foundation.
