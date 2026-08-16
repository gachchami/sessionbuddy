-- Record how a decision-email subject was chosen without guessing provenance for
-- messages written by older releases or by unrelated communication workflows.

ALTER TABLE communication_messages ADD COLUMN subject_source TEXT
  CHECK (subject_source IN ('builtin', 'override', 'event_template'));
