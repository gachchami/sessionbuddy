PRAGMA foreign_keys = ON;

-- CFP availability, capacity, completion copy, and routing remain attached to
-- the immutable published form version that applicants actually completed.
ALTER TABLE call_for_speaker_forms ADD COLUMN opens_at_ms INTEGER;
ALTER TABLE call_for_speaker_forms ADD COLUMN closes_at_ms INTEGER;
ALTER TABLE call_for_speaker_forms ADD COLUMN submission_limit INTEGER
  CHECK (submission_limit IS NULL OR submission_limit BETWEEN 1 AND 1000000);
ALTER TABLE call_for_speaker_forms ADD COLUMN success_title TEXT NOT NULL
  DEFAULT 'Proposal received' CHECK (length(success_title) BETWEEN 1 AND 200);
ALTER TABLE call_for_speaker_forms ADD COLUMN success_message TEXT NOT NULL
  DEFAULT 'We sent a confirmation to your email address.'
  CHECK (length(success_message) BETWEEN 1 AND 2000);
ALTER TABLE call_for_speaker_forms ADD COLUMN redirect_to_portal INTEGER NOT NULL
  DEFAULT 1 CHECK (redirect_to_portal IN (0, 1));
ALTER TABLE call_for_speaker_forms ADD COLUMN confirmation_subject TEXT NOT NULL
  DEFAULT 'We received your proposal' CHECK (length(confirmation_subject) BETWEEN 1 AND 200);
ALTER TABLE call_for_speaker_forms ADD COLUMN confirmation_body TEXT NOT NULL
  DEFAULT 'Thank you for submitting. Your proposal is now ready for review.'
  CHECK (length(confirmation_body) BETWEEN 1 AND 4000);

ALTER TABLE submissions ADD COLUMN routed_category TEXT;
ALTER TABLE submissions ADD COLUMN routed_track TEXT;
ALTER TABLE submissions ADD COLUMN routed_review_queue TEXT;

-- The legacy status tracks onboarding completion. Selection is separate so a
-- submitter can use the portal before a final acceptance decision exists.
ALTER TABLE event_speakers ADD COLUMN selection_status TEXT NOT NULL DEFAULT 'accepted'
  CHECK (selection_status IN ('submitted', 'accepted', 'rejected'));

ALTER TABLE speaker_tasks ADD COLUMN form_schema_json TEXT
  CHECK (form_schema_json IS NULL OR json_valid(form_schema_json));
ALTER TABLE speaker_tasks ADD COLUMN response_json TEXT
  CHECK (response_json IS NULL OR json_valid(response_json));
ALTER TABLE speaker_tasks ADD COLUMN responded_at_ms INTEGER;

CREATE TABLE event_resources (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
  slug TEXT NOT NULL CHECK (length(slug) BETWEEN 3 AND 80),
  summary TEXT NOT NULL DEFAULT '' CHECK (length(summary) <= 500),
  body_text TEXT NOT NULL DEFAULT '' CHECK (length(body_text) <= 20000),
  embed_url TEXT CHECK (embed_url IS NULL OR length(embed_url) <= 2000),
  status TEXT NOT NULL CHECK (status IN ('draft', 'published', 'archived')),
  sort_order INTEGER NOT NULL DEFAULT 0,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_by_user_id TEXT NOT NULL,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  published_at_ms INTEGER,
  FOREIGN KEY (organization_id,event_id)
    REFERENCES events(organization_id,id) ON DELETE RESTRICT,
  FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  UNIQUE (organization_id,event_id,id),
  UNIQUE (organization_id,event_id,slug),
  CHECK ((status='published') = (published_at_ms IS NOT NULL))
);

CREATE TABLE event_integration_tokens (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  provider TEXT NOT NULL CHECK (provider IN ('accelevents')),
  label TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 120),
  token_hash BLOB NOT NULL CHECK (length(token_hash) = 32),
  status TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
  created_by_user_id TEXT NOT NULL,
  created_at_ms INTEGER NOT NULL,
  last_used_at_ms INTEGER,
  revoked_at_ms INTEGER,
  FOREIGN KEY (organization_id,event_id)
    REFERENCES events(organization_id,id) ON DELETE RESTRICT,
  FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  UNIQUE (token_hash),
  UNIQUE (organization_id,event_id,id),
  CHECK ((status='revoked') = (revoked_at_ms IS NOT NULL))
);

-- Converts a conditional INSERT that wrote zero rows into an atomic D1 batch
-- failure, so concurrent submitters cannot exceed a published form's limit.
CREATE TABLE submission_write_guards (
  id TEXT PRIMARY KEY NOT NULL,
  submission_id TEXT NOT NULL,
  applied_changes INTEGER NOT NULL CHECK (applied_changes = 1),
  created_at_ms INTEGER NOT NULL
);

ALTER TABLE events ADD COLUMN logo_url TEXT;
ALTER TABLE events ADD COLUMN website_url TEXT;

CREATE INDEX idx_forms_public_availability
  ON call_for_speaker_forms(slug,status,opens_at_ms,closes_at_ms);
CREATE INDEX idx_submissions_form_count
  ON submissions(form_id,status,submitted_at_ms,id);
CREATE INDEX idx_event_speakers_selection
  ON event_speakers(organization_id,event_id,selection_status,last_activity_at_ms DESC,id DESC);
CREATE INDEX idx_event_resources_portal
  ON event_resources(organization_id,event_id,status,sort_order,title,id);
CREATE INDEX idx_integration_tokens_lookup
  ON event_integration_tokens(token_hash,status,event_id);
