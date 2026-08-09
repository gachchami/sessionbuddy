-- Move CFP and evaluation ownership directly to events.
-- Guard first so collapsing the program dimension cannot merge conflicting rows.

PRAGMA defer_foreign_keys = ON;

CREATE TABLE migration_0034_guard (
  ok INTEGER NOT NULL CHECK (ok = 1)
);

INSERT INTO migration_0034_guard(ok)
SELECT 0
FROM call_for_speaker_forms
GROUP BY organization_id,event_id,version
HAVING COUNT(*) > 1
LIMIT 1;

INSERT INTO migration_0034_guard(ok)
SELECT 0
FROM evaluation_rounds
WHERE status = 'open'
GROUP BY organization_id,event_id
HAVING COUNT(*) > 1
LIMIT 1;

DROP TABLE migration_0034_guard;

DROP TRIGGER validate_evaluation_assignment_scope_insert;
DROP TRIGGER validate_evaluation_assignment_scope_update;
DROP TRIGGER validate_submission_decision_scope_insert;
DROP TRIGGER validate_submission_decision_scope_update;
DROP TRIGGER validate_submission_form_scope_insert;
DROP TRIGGER validate_submission_form_scope_update;
DROP TRIGGER validate_submission_draft_scope_insert;
DROP TRIGGER validate_submission_draft_scope_update;

CREATE TABLE call_for_speaker_forms_event_owned (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  version INTEGER NOT NULL CHECK (version >= 1),
  slug TEXT NOT NULL CHECK (length(slug) BETWEEN 3 AND 80),
  welcome_text TEXT NOT NULL CHECK (length(welcome_text) BETWEEN 1 AND 1000),
  schema_json TEXT NOT NULL CHECK (json_valid(schema_json)),
  status TEXT NOT NULL CHECK (status IN ('draft', 'published', 'closed')),
  published_at_ms INTEGER,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  opens_at_ms INTEGER,
  closes_at_ms INTEGER,
  submission_limit INTEGER
    CHECK (submission_limit IS NULL OR submission_limit BETWEEN 1 AND 1000000),
  success_title TEXT NOT NULL DEFAULT 'Proposal received'
    CHECK (length(success_title) BETWEEN 1 AND 200),
  success_message TEXT NOT NULL DEFAULT 'We sent a confirmation to your email address.'
    CHECK (length(success_message) BETWEEN 1 AND 2000),
  redirect_to_portal INTEGER NOT NULL DEFAULT 1 CHECK (redirect_to_portal IN (0, 1)),
  confirmation_subject TEXT NOT NULL DEFAULT 'We received your proposal'
    CHECK (length(confirmation_subject) BETWEEN 1 AND 200),
  confirmation_body TEXT NOT NULL
    DEFAULT 'Thank you for submitting. Your proposal is now ready for review.'
    CHECK (length(confirmation_body) BETWEEN 1 AND 4000),
  FOREIGN KEY (organization_id,event_id)
    REFERENCES events(organization_id,id) ON DELETE RESTRICT,
  UNIQUE (slug),
  UNIQUE (organization_id,event_id,version),
  CHECK ((status = 'published') = (published_at_ms IS NOT NULL))
);

INSERT INTO call_for_speaker_forms_event_owned
  (id,organization_id,event_id,version,slug,welcome_text,schema_json,status,
   published_at_ms,created_at_ms,updated_at_ms,opens_at_ms,closes_at_ms,
   submission_limit,success_title,success_message,redirect_to_portal,
   confirmation_subject,confirmation_body)
SELECT id,organization_id,event_id,version,slug,welcome_text,schema_json,status,
       published_at_ms,created_at_ms,updated_at_ms,opens_at_ms,closes_at_ms,
       submission_limit,success_title,success_message,redirect_to_portal,
       confirmation_subject,confirmation_body
FROM call_for_speaker_forms;

DROP TABLE call_for_speaker_forms;
ALTER TABLE call_for_speaker_forms_event_owned RENAME TO call_for_speaker_forms;

CREATE INDEX idx_cfp_forms_event_version
  ON call_for_speaker_forms(organization_id,event_id,version DESC);
CREATE INDEX idx_forms_public_availability
  ON call_for_speaker_forms(slug,status,opens_at_ms,closes_at_ms);
CREATE INDEX idx_cfp_forms_event_published
  ON call_for_speaker_forms(
    organization_id,event_id,version DESC,published_at_ms DESC,id DESC
  ) WHERE status = 'published';

CREATE TRIGGER validate_cfp_form_window_insert
BEFORE INSERT ON call_for_speaker_forms
WHEN NEW.opens_at_ms IS NOT NULL AND NEW.closes_at_ms IS NOT NULL
  AND NEW.closes_at_ms <= NEW.opens_at_ms
BEGIN
  SELECT RAISE(ABORT, 'CFP closing time must be after opening time');
END;

CREATE TRIGGER validate_cfp_form_window_update
BEFORE UPDATE OF opens_at_ms,closes_at_ms ON call_for_speaker_forms
WHEN NEW.opens_at_ms IS NOT NULL AND NEW.closes_at_ms IS NOT NULL
  AND NEW.closes_at_ms <= NEW.opens_at_ms
BEGIN
  SELECT RAISE(ABORT, 'CFP closing time must be after opening time');
END;

CREATE TABLE submissions_event_owned (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  form_id TEXT NOT NULL,
  public_session_id TEXT NOT NULL,
  proposal_title TEXT NOT NULL CHECK (length(proposal_title) BETWEEN 1 AND 200),
  proposal_abstract TEXT NOT NULL CHECK (length(proposal_abstract) BETWEEN 1 AND 5000),
  speaker_name TEXT NOT NULL CHECK (length(speaker_name) BETWEEN 1 AND 200),
  status TEXT NOT NULL CHECK (status IN ('submitted', 'withdrawn')),
  submitted_at_ms INTEGER NOT NULL,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  speaker_email TEXT NOT NULL DEFAULT '',
  submitter_user_id TEXT REFERENCES users(id),
  answers_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(answers_json)),
  routed_category TEXT,
  routed_track TEXT,
  routed_review_queue TEXT,
  version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
  FOREIGN KEY (organization_id,event_id)
    REFERENCES events(organization_id,id) ON DELETE RESTRICT,
  FOREIGN KEY (form_id) REFERENCES call_for_speaker_forms(id) ON DELETE RESTRICT,
  UNIQUE (public_session_id,form_id),
  UNIQUE (organization_id,event_id,id)
);

INSERT INTO submissions_event_owned
  (id,organization_id,event_id,form_id,public_session_id,proposal_title,
   proposal_abstract,speaker_name,status,submitted_at_ms,created_at_ms,updated_at_ms,
   speaker_email,submitter_user_id,answers_json,routed_category,routed_track,
   routed_review_queue,version)
SELECT id,organization_id,event_id,form_id,public_session_id,proposal_title,
       proposal_abstract,speaker_name,status,submitted_at_ms,created_at_ms,updated_at_ms,
       speaker_email,submitter_user_id,answers_json,routed_category,routed_track,
       routed_review_queue,version
FROM submissions;


CREATE TABLE submission_contributors_event_owned (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  submission_id TEXT NOT NULL,
  display_name TEXT NOT NULL CHECK(length(display_name) BETWEEN 1 AND 200),
  email TEXT NOT NULL CHECK(length(email) BETWEEN 3 AND 320),
  normalized_email TEXT NOT NULL CHECK(length(normalized_email) BETWEEN 3 AND 320),
  role TEXT NOT NULL DEFAULT 'co_speaker' CHECK(role='co_speaker'),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id,event_id,submission_id)
    REFERENCES submissions_event_owned(organization_id,event_id,id) ON DELETE CASCADE,
  UNIQUE (submission_id,normalized_email)
);

INSERT INTO submission_contributors_event_owned
SELECT id,organization_id,event_id,submission_id,display_name,email,normalized_email,
       role,created_at_ms,updated_at_ms
FROM submission_contributors;

CREATE TABLE ai_triage_results_event_owned (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  submission_id TEXT NOT NULL,
  model TEXT NOT NULL CHECK(length(model) BETWEEN 1 AND 200),
  score INTEGER NOT NULL CHECK(score BETWEEN 0 AND 10),
  recommendation TEXT NOT NULL CHECK(length(recommendation) BETWEEN 1 AND 80),
  rationale TEXT NOT NULL CHECK(length(rationale) BETWEEN 1 AND 4000),
  generated_by_user_id TEXT NOT NULL,
  generated_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id,event_id,submission_id)
    REFERENCES submissions_event_owned(organization_id,event_id,id) ON DELETE CASCADE,
  FOREIGN KEY (generated_by_user_id) REFERENCES users(id)
);

INSERT INTO ai_triage_results_event_owned
SELECT id,organization_id,event_id,submission_id,model,score,recommendation,rationale,
       generated_by_user_id,generated_at_ms
FROM ai_triage_results;

DROP TABLE submission_contributors;
DROP TABLE ai_triage_results;



DROP TABLE submissions;
ALTER TABLE submissions_event_owned RENAME TO submissions;


ALTER TABLE submission_contributors_event_owned RENAME TO submission_contributors;
ALTER TABLE ai_triage_results_event_owned RENAME TO ai_triage_results;

CREATE INDEX idx_submission_contributors_submission
  ON submission_contributors(organization_id,event_id,submission_id,display_name,id);
CREATE INDEX idx_ai_triage_submission_recent
  ON ai_triage_results(organization_id,event_id,submission_id,generated_at_ms DESC,id DESC);


CREATE INDEX idx_submissions_event_recent
  ON submissions(organization_id,event_id,submitted_at_ms DESC,id DESC);
CREATE INDEX idx_submissions_submitter
  ON submissions(submitter_user_id,submitted_at_ms DESC,id DESC);
CREATE INDEX idx_submissions_form_count
  ON submissions(form_id,status,submitted_at_ms,id);
CREATE INDEX idx_submissions_form_owner_recent
  ON submissions(form_id,submitter_user_id,status,updated_at_ms DESC,id DESC);

CREATE TRIGGER validate_submission_form_scope_insert
BEFORE INSERT ON submissions
WHEN NOT EXISTS (
  SELECT 1 FROM call_for_speaker_forms f
  WHERE f.id=NEW.form_id AND f.organization_id=NEW.organization_id
    AND f.event_id=NEW.event_id
)
BEGIN
  SELECT RAISE(ABORT, 'submission form scope mismatch');
END;

CREATE TRIGGER validate_submission_form_scope_update
BEFORE UPDATE OF organization_id,event_id,form_id ON submissions
WHEN NOT EXISTS (
  SELECT 1 FROM call_for_speaker_forms f
  WHERE f.id=NEW.form_id AND f.organization_id=NEW.organization_id
    AND f.event_id=NEW.event_id
)
BEGIN
  SELECT RAISE(ABORT, 'submission form scope mismatch');
END;

CREATE TRIGGER validate_submission_owner_insert
BEFORE INSERT ON submissions
WHEN NEW.submitter_user_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM organization_memberships m
  WHERE m.organization_id=NEW.organization_id AND m.user_id=NEW.submitter_user_id
    AND m.status='active'
)
BEGIN
  SELECT RAISE(ABORT, 'submission owner scope mismatch');
END;

CREATE TRIGGER validate_submission_owner_update
BEFORE UPDATE OF organization_id,submitter_user_id ON submissions
WHEN NEW.submitter_user_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM organization_memberships m
  WHERE m.organization_id=NEW.organization_id AND m.user_id=NEW.submitter_user_id
    AND m.status='active'
)
BEGIN
  SELECT RAISE(ABORT, 'submission owner scope mismatch');
END;

CREATE TABLE submission_drafts_event_owned (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  form_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  answers_json TEXT NOT NULL CHECK(json_valid(answers_json)),
  version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id,event_id)
    REFERENCES events(organization_id,id) ON DELETE RESTRICT,
  FOREIGN KEY (form_id) REFERENCES call_for_speaker_forms(id),
  FOREIGN KEY (user_id) REFERENCES users(id),
  UNIQUE (form_id,user_id)
);

INSERT INTO submission_drafts_event_owned
  (id,organization_id,event_id,form_id,user_id,answers_json,version,created_at_ms,updated_at_ms)
SELECT id,organization_id,event_id,form_id,user_id,answers_json,version,created_at_ms,updated_at_ms
FROM submission_drafts;

DROP TABLE submission_drafts;
ALTER TABLE submission_drafts_event_owned RENAME TO submission_drafts;

CREATE INDEX idx_submission_drafts_user
  ON submission_drafts(user_id,updated_at_ms DESC,id DESC);
CREATE INDEX idx_submission_drafts_event
  ON submission_drafts(organization_id,event_id,updated_at_ms DESC,id DESC);

CREATE TRIGGER validate_submission_draft_scope_insert
BEFORE INSERT ON submission_drafts
WHEN NOT EXISTS (
  SELECT 1 FROM call_for_speaker_forms f
  WHERE f.id=NEW.form_id AND f.organization_id=NEW.organization_id
    AND f.event_id=NEW.event_id
)
BEGIN
  SELECT RAISE(ABORT, 'submission draft scope mismatch');
END;

CREATE TRIGGER validate_submission_draft_scope_update
BEFORE UPDATE OF organization_id,event_id,form_id ON submission_drafts
WHEN NOT EXISTS (
  SELECT 1 FROM call_for_speaker_forms f
  WHERE f.id=NEW.form_id AND f.organization_id=NEW.organization_id
    AND f.event_id=NEW.event_id
)
BEGIN
  SELECT RAISE(ABORT, 'submission draft scope mismatch');
END;

CREATE TRIGGER validate_submission_draft_owner_insert
BEFORE INSERT ON submission_drafts
WHEN NOT EXISTS (
  SELECT 1 FROM organization_memberships m
  WHERE m.organization_id=NEW.organization_id AND m.user_id=NEW.user_id
    AND m.status='active'
)
BEGIN
  SELECT RAISE(ABORT, 'submission draft owner scope mismatch');
END;

CREATE TRIGGER validate_submission_draft_owner_update
BEFORE UPDATE OF organization_id,user_id ON submission_drafts
WHEN NOT EXISTS (
  SELECT 1 FROM organization_memberships m
  WHERE m.organization_id=NEW.organization_id AND m.user_id=NEW.user_id
    AND m.status='active'
)
BEGIN
  SELECT RAISE(ABORT, 'submission draft owner scope mismatch');
END;

CREATE TABLE evaluation_rounds_event_owned (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  rubric_json TEXT NOT NULL CHECK (json_valid(rubric_json)),
  status TEXT NOT NULL CHECK (status IN ('draft', 'open', 'closed')),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  closed_at_ms INTEGER,
  review_opens_at_ms INTEGER,
  review_closes_at_ms INTEGER,
  FOREIGN KEY (organization_id,event_id)
    REFERENCES events(organization_id,id) ON DELETE RESTRICT,
  CHECK ((status = 'closed') = (closed_at_ms IS NOT NULL))
);

INSERT INTO evaluation_rounds_event_owned
  (id,organization_id,event_id,name,rubric_json,status,created_at_ms,updated_at_ms,
   closed_at_ms,review_opens_at_ms,review_closes_at_ms)
SELECT id,organization_id,event_id,name,rubric_json,status,created_at_ms,updated_at_ms,
       closed_at_ms,review_opens_at_ms,review_closes_at_ms
FROM evaluation_rounds;

DROP TABLE evaluation_rounds;
ALTER TABLE evaluation_rounds_event_owned RENAME TO evaluation_rounds;

CREATE INDEX idx_evaluation_rounds_event
  ON evaluation_rounds(organization_id,event_id,status,created_at_ms DESC);
CREATE UNIQUE INDEX uq_evaluation_rounds_event_open
  ON evaluation_rounds(organization_id,event_id) WHERE status='open';
CREATE INDEX idx_evaluation_rounds_review_window
  ON evaluation_rounds(status,review_opens_at_ms,review_closes_at_ms,event_id);

CREATE TRIGGER validate_evaluation_round_window_insert
BEFORE INSERT ON evaluation_rounds
WHEN NEW.review_opens_at_ms IS NOT NULL AND NEW.review_closes_at_ms IS NOT NULL
  AND NEW.review_closes_at_ms <= NEW.review_opens_at_ms
BEGIN
  SELECT RAISE(ABORT, 'review close must be after review open');
END;

CREATE TRIGGER validate_evaluation_round_window_update
BEFORE UPDATE OF review_opens_at_ms,review_closes_at_ms ON evaluation_rounds
WHEN NEW.review_opens_at_ms IS NOT NULL AND NEW.review_closes_at_ms IS NOT NULL
  AND NEW.review_closes_at_ms <= NEW.review_opens_at_ms
BEGIN
  SELECT RAISE(ABORT, 'review close must be after review open');
END;

CREATE TRIGGER validate_evaluation_assignment_scope_insert
BEFORE INSERT ON evaluation_assignments
WHEN NOT EXISTS (
  SELECT 1 FROM evaluation_rounds r
  JOIN submissions s ON s.id=NEW.submission_id
  JOIN event_memberships m ON m.organization_id=NEW.organization_id
    AND m.event_id=NEW.event_id AND m.user_id=NEW.evaluator_user_id
    AND m.role='evaluator' AND m.status='active'
  WHERE r.id=NEW.round_id AND r.organization_id=NEW.organization_id
    AND r.event_id=NEW.event_id AND s.organization_id=NEW.organization_id
    AND s.event_id=NEW.event_id
)
BEGIN
  SELECT RAISE(ABORT, 'evaluation assignment scope mismatch');
END;

CREATE TRIGGER validate_evaluation_assignment_scope_update
BEFORE UPDATE OF organization_id,event_id,round_id,submission_id,evaluator_user_id
ON evaluation_assignments
WHEN NOT EXISTS (
  SELECT 1 FROM evaluation_rounds r
  JOIN submissions s ON s.id=NEW.submission_id
  JOIN event_memberships m ON m.organization_id=NEW.organization_id
    AND m.event_id=NEW.event_id AND m.user_id=NEW.evaluator_user_id
    AND m.role='evaluator' AND m.status='active'
  WHERE r.id=NEW.round_id AND r.organization_id=NEW.organization_id
    AND r.event_id=NEW.event_id AND s.organization_id=NEW.organization_id
    AND s.event_id=NEW.event_id
)
BEGIN
  SELECT RAISE(ABORT, 'evaluation assignment scope mismatch');
END;

CREATE TRIGGER validate_submission_decision_scope_insert
BEFORE INSERT ON submission_decisions
WHEN NOT EXISTS (
  SELECT 1 FROM evaluation_rounds r
  JOIN submissions s ON s.id=NEW.submission_id
  WHERE r.id=NEW.round_id AND r.organization_id=NEW.organization_id
    AND r.event_id=NEW.event_id AND s.organization_id=NEW.organization_id
    AND s.event_id=NEW.event_id
)
BEGIN
  SELECT RAISE(ABORT, 'submission decision scope mismatch');
END;

CREATE TRIGGER validate_submission_decision_scope_update
BEFORE UPDATE OF organization_id,event_id,round_id,submission_id ON submission_decisions
WHEN NOT EXISTS (
  SELECT 1 FROM evaluation_rounds r
  JOIN submissions s ON s.id=NEW.submission_id
  WHERE r.id=NEW.round_id AND r.organization_id=NEW.organization_id
    AND r.event_id=NEW.event_id AND s.organization_id=NEW.organization_id
    AND s.event_id=NEW.event_id
)
BEGIN
  SELECT RAISE(ABORT, 'submission decision scope mismatch');
END;

DROP TABLE programs;

PRAGMA defer_foreign_keys = OFF;
