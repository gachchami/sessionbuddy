PRAGMA foreign_keys = ON;

CREATE TABLE call_for_speaker_forms (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  program_id TEXT NOT NULL,
  version INTEGER NOT NULL CHECK (version >= 1),
  slug TEXT NOT NULL CHECK (length(slug) BETWEEN 3 AND 80),
  welcome_text TEXT NOT NULL CHECK (length(welcome_text) BETWEEN 1 AND 1000),
  schema_json TEXT NOT NULL CHECK (json_valid(schema_json)),
  status TEXT NOT NULL CHECK (status IN ('draft', 'published', 'closed')),
  published_at_ms INTEGER,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id, event_id, program_id)
    REFERENCES programs(organization_id, event_id, id) ON DELETE RESTRICT,
  UNIQUE (slug),
  UNIQUE (organization_id, event_id, program_id, version),
  CHECK ((status = 'published') = (published_at_ms IS NOT NULL))
);

CREATE TABLE submissions (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  program_id TEXT NOT NULL,
  form_id TEXT NOT NULL,
  public_session_id TEXT NOT NULL,
  proposal_title TEXT NOT NULL CHECK (length(proposal_title) BETWEEN 1 AND 200),
  proposal_abstract TEXT NOT NULL CHECK (length(proposal_abstract) BETWEEN 1 AND 5000),
  speaker_name TEXT NOT NULL CHECK (length(speaker_name) BETWEEN 1 AND 200),
  status TEXT NOT NULL CHECK (status IN ('submitted', 'withdrawn')),
  submitted_at_ms INTEGER NOT NULL,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id, event_id, program_id)
    REFERENCES programs(organization_id, event_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (form_id) REFERENCES call_for_speaker_forms(id) ON DELETE RESTRICT,
  UNIQUE (public_session_id, form_id)
);

CREATE INDEX idx_cfp_forms_program_version
  ON call_for_speaker_forms(organization_id, event_id, program_id, version DESC);
CREATE INDEX idx_submissions_program_recent
  ON submissions(organization_id, event_id, program_id, submitted_at_ms DESC, id DESC);
