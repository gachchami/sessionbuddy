-- Add audited decision corrections and accepted-session lifecycle state.
-- This migration is additive: historical sessions retain their immutable
-- decision_id, gain no correction, and remain active.

CREATE TABLE submission_decision_corrections (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  submission_id TEXT NOT NULL,
  original_decision_id TEXT NOT NULL,
  previous_decision TEXT NOT NULL CHECK (previous_decision IN ('accepted','rejected')),
  corrected_decision TEXT NOT NULL CHECK (corrected_decision IN ('accepted','rejected')),
  reason TEXT NOT NULL CHECK (length(trim(reason)) BETWEEN 1 AND 2000),
  corrected_by_user_id TEXT NOT NULL,
  corrected_at_ms INTEGER NOT NULL,
  CHECK (previous_decision != corrected_decision),
  FOREIGN KEY (submission_id) REFERENCES submissions(id) ON DELETE RESTRICT,
  FOREIGN KEY (original_decision_id) REFERENCES submission_decisions(id) ON DELETE RESTRICT,
  FOREIGN KEY (corrected_by_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  UNIQUE (organization_id,event_id,id)
);

CREATE INDEX idx_submission_decision_corrections_effective
  ON submission_decision_corrections(
    organization_id,event_id,submission_id,corrected_at_ms DESC,id DESC
  );

ALTER TABLE accepted_sessions ADD COLUMN decision_correction_id TEXT
  REFERENCES submission_decision_corrections(id) ON DELETE RESTRICT;
ALTER TABLE accepted_sessions ADD COLUMN lifecycle_status TEXT NOT NULL DEFAULT 'active'
  CHECK (lifecycle_status IN ('active', 'withdrawn'));
ALTER TABLE accepted_sessions ADD COLUMN withdrawn_at_ms INTEGER;

DROP TRIGGER validate_accepted_session_decision;
DROP TRIGGER validate_accepted_session_decision_update;

CREATE TRIGGER validate_accepted_session_decision
BEFORE INSERT ON accepted_sessions
WHEN NEW.source_type='accepted_proposal' AND NOT (
  (NEW.decision_correction_id IS NULL AND EXISTS (
    SELECT 1 FROM submission_decisions d
    WHERE d.id=NEW.decision_id AND d.organization_id=NEW.organization_id
      AND d.event_id=NEW.event_id AND d.submission_id=NEW.submission_id
      AND d.decision='accepted'))
  OR
  (NEW.decision_correction_id IS NOT NULL AND EXISTS (
    SELECT 1 FROM submission_decision_corrections c
    WHERE c.id=NEW.decision_correction_id AND c.original_decision_id=NEW.decision_id
      AND c.organization_id=NEW.organization_id AND c.event_id=NEW.event_id
      AND c.submission_id=NEW.submission_id AND c.corrected_decision='accepted'))
)
BEGIN
  SELECT RAISE(ABORT, 'accepted decision required');
END;

CREATE TRIGGER validate_accepted_session_decision_update
BEFORE UPDATE OF organization_id,event_id,submission_id,decision_id,decision_correction_id
ON accepted_sessions
WHEN NEW.source_type='accepted_proposal' AND NOT (
  (NEW.decision_correction_id IS NULL AND EXISTS (
    SELECT 1 FROM submission_decisions d
    WHERE d.id=NEW.decision_id AND d.organization_id=NEW.organization_id
      AND d.event_id=NEW.event_id AND d.submission_id=NEW.submission_id
      AND d.decision='accepted'))
  OR
  (NEW.decision_correction_id IS NOT NULL AND EXISTS (
    SELECT 1 FROM submission_decision_corrections c
    WHERE c.id=NEW.decision_correction_id AND c.original_decision_id=NEW.decision_id
      AND c.organization_id=NEW.organization_id AND c.event_id=NEW.event_id
      AND c.submission_id=NEW.submission_id AND c.corrected_decision='accepted'))
)
BEGIN
  SELECT RAISE(ABORT, 'accepted decision required');
END;

CREATE TRIGGER validate_accepted_session_lifecycle_insert
BEFORE INSERT ON accepted_sessions
WHEN NOT (
  (NEW.lifecycle_status='active' AND NEW.withdrawn_at_ms IS NULL)
  OR (NEW.lifecycle_status='withdrawn' AND NEW.withdrawn_at_ms IS NOT NULL)
)
BEGIN
  SELECT RAISE(ABORT, 'accepted session lifecycle mismatch');
END;

CREATE TRIGGER validate_accepted_session_lifecycle_update
BEFORE UPDATE OF lifecycle_status,withdrawn_at_ms ON accepted_sessions
WHEN NOT (
  (NEW.lifecycle_status='active' AND NEW.withdrawn_at_ms IS NULL)
  OR (NEW.lifecycle_status='withdrawn' AND NEW.withdrawn_at_ms IS NOT NULL)
)
BEGIN
  SELECT RAISE(ABORT, 'accepted session lifecycle mismatch');
END;

