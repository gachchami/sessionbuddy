PRAGMA foreign_keys = ON;

-- Existing Wave 1 tables predate the composite-key extension rule. This index
-- lets every new speaker relationship prove that its submission belongs to the
-- same organization and event.
CREATE UNIQUE INDEX uq_submissions_tenant_id
  ON submissions(organization_id, event_id, id);

CREATE TABLE people (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  user_id TEXT,
  display_name TEXT NOT NULL CHECK (length(display_name) BETWEEN 1 AND 200),
  job_title TEXT CHECK (job_title IS NULL OR length(job_title) <= 200),
  company TEXT CHECK (company IS NULL OR length(company) <= 200),
  biography TEXT CHECK (biography IS NULL OR length(biography) <= 5000),
  location TEXT CHECK (location IS NULL OR length(location) <= 300),
  links_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(links_json)),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  archived_at_ms INTEGER,
  FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, user_id)
    REFERENCES organization_memberships(organization_id, user_id) ON DELETE RESTRICT,
  UNIQUE (organization_id, id),
  UNIQUE (organization_id, user_id)
);

CREATE TABLE event_speakers (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  person_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('onboarding', 'complete', 'withdrawn')),
  accepted_at_ms INTEGER NOT NULL,
  last_activity_at_ms INTEGER NOT NULL,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  withdrawn_at_ms INTEGER,
  FOREIGN KEY (organization_id, event_id)
    REFERENCES events(organization_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, person_id)
    REFERENCES people(organization_id, id) ON DELETE RESTRICT,
  UNIQUE (organization_id, event_id, id),
  UNIQUE (organization_id, event_id, person_id),
  CHECK ((status = 'withdrawn') = (withdrawn_at_ms IS NOT NULL))
);

CREATE TABLE submission_speakers (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  submission_id TEXT NOT NULL,
  event_speaker_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('primary', 'co_speaker')),
  snapshot_name TEXT NOT NULL CHECK (length(snapshot_name) BETWEEN 1 AND 200),
  created_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id, event_id, submission_id)
    REFERENCES submissions(organization_id, event_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, event_id, event_speaker_id)
    REFERENCES event_speakers(organization_id, event_id, id) ON DELETE RESTRICT,
  UNIQUE (organization_id, event_id, submission_id, event_speaker_id)
);

CREATE UNIQUE INDEX uq_submission_speakers_primary
  ON submission_speakers(organization_id, event_id, submission_id)
  WHERE role = 'primary';

CREATE TABLE speaker_tasks (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  event_speaker_id TEXT NOT NULL,
  submission_id TEXT,
  task_type TEXT NOT NULL
    CHECK (task_type IN ('profile', 'headshot', 'slides', 'supporting_document', 'custom')),
  title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
  help_text TEXT CHECK (help_text IS NULL OR length(help_text) <= 2000),
  destination_type TEXT NOT NULL
    CHECK (destination_type IN ('profile', 'headshot', 'slides', 'supporting_document', 'custom')),
  state TEXT NOT NULL CHECK (state IN ('open', 'completed', 'waived')),
  due_at_ms INTEGER,
  completed_at_ms INTEGER,
  waived_at_ms INTEGER,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id, event_id, event_speaker_id)
    REFERENCES event_speakers(organization_id, event_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, event_id, submission_id)
    REFERENCES submissions(organization_id, event_id, id) ON DELETE RESTRICT,
  UNIQUE (organization_id, event_id, id),
  CHECK ((state = 'completed') = (completed_at_ms IS NOT NULL)),
  CHECK ((state = 'waived') = (waived_at_ms IS NOT NULL)),
  CHECK (completed_at_ms IS NULL OR waived_at_ms IS NULL)
);

CREATE INDEX idx_people_org_user
  ON people(organization_id, user_id);
CREATE INDEX idx_event_speakers_event_status_activity
  ON event_speakers(organization_id, event_id, status, last_activity_at_ms DESC, id DESC);
CREATE INDEX idx_submission_speakers_speaker
  ON submission_speakers(organization_id, event_id, event_speaker_id, created_at_ms DESC, id DESC);
CREATE INDEX idx_speaker_tasks_portal
  ON speaker_tasks(organization_id, event_id, event_speaker_id, state, due_at_ms, id);
CREATE INDEX idx_speaker_tasks_dashboard
  ON speaker_tasks(organization_id, event_id, state, due_at_ms, id);
CREATE INDEX idx_speaker_tasks_submission
  ON speaker_tasks(organization_id, event_id, submission_id, state, id);
