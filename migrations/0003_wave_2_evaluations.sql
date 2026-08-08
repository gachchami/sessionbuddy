PRAGMA foreign_keys = ON;

CREATE TABLE evaluation_rounds (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  program_id TEXT NOT NULL,
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  rubric_json TEXT NOT NULL CHECK (json_valid(rubric_json)),
  status TEXT NOT NULL CHECK (status IN ('draft', 'open', 'closed')),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  closed_at_ms INTEGER,
  FOREIGN KEY (organization_id, event_id, program_id)
    REFERENCES programs(organization_id, event_id, id) ON DELETE RESTRICT,
  CHECK ((status = 'closed') = (closed_at_ms IS NOT NULL))
);

CREATE TABLE evaluation_assignments (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  round_id TEXT NOT NULL,
  submission_id TEXT NOT NULL,
  evaluator_user_id TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'evaluator' CHECK (role = 'evaluator'),
  status TEXT NOT NULL CHECK (status IN ('assigned', 'completed', 'revoked')),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  FOREIGN KEY (round_id) REFERENCES evaluation_rounds(id) ON DELETE RESTRICT,
  FOREIGN KEY (submission_id) REFERENCES submissions(id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, event_id, evaluator_user_id, role)
    REFERENCES event_memberships(organization_id, event_id, user_id, role) ON DELETE RESTRICT,
  UNIQUE (round_id, submission_id, evaluator_user_id)
);

CREATE TABLE evaluations (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  round_id TEXT NOT NULL,
  assignment_id TEXT NOT NULL,
  evaluator_user_id TEXT NOT NULL,
  rating INTEGER NOT NULL,
  recommendation TEXT NOT NULL CHECK (length(recommendation) BETWEEN 1 AND 80),
  internal_comment TEXT NOT NULL CHECK (length(internal_comment) <= 5000),
  state TEXT NOT NULL CHECK (state IN ('draft', 'final')),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  finalized_at_ms INTEGER,
  FOREIGN KEY (round_id) REFERENCES evaluation_rounds(id) ON DELETE RESTRICT,
  FOREIGN KEY (assignment_id) REFERENCES evaluation_assignments(id) ON DELETE RESTRICT,
  UNIQUE (assignment_id),
  CHECK ((state = 'final') = (finalized_at_ms IS NOT NULL))
);

CREATE INDEX idx_evaluation_rounds_program
  ON evaluation_rounds(organization_id, event_id, program_id, status, created_at_ms DESC);
CREATE INDEX idx_evaluation_assignments_evaluator
  ON evaluation_assignments(evaluator_user_id, status, created_at_ms DESC, id DESC);
CREATE INDEX idx_evaluations_round
  ON evaluations(round_id, state, updated_at_ms DESC);
