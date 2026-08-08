CREATE TABLE communication_templates (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 120),
  kind TEXT NOT NULL CHECK(kind IN ('submission_confirmation','decision','task_assignment','task_reminder','schedule_change','manual')),
  subject_template TEXT NOT NULL CHECK(length(subject_template) BETWEEN 1 AND 300),
  html_template TEXT NOT NULL CHECK(length(html_template) BETWEEN 1 AND 50000),
  version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id,event_id) REFERENCES events(organization_id,id) ON DELETE RESTRICT,
  UNIQUE (organization_id,event_id,id)
);

CREATE TABLE communication_messages (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  template_id TEXT,
  recipient_user_id TEXT,
  recipient_email TEXT NOT NULL,
  subject TEXT NOT NULL,
  html_body TEXT NOT NULL,
  deterministic_key TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('queued','sending','delivered','failed','cancelled')),
  provider_message_id TEXT,
  last_error_code TEXT,
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
  queued_at_ms INTEGER NOT NULL,
  delivered_at_ms INTEGER,
  updated_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id,event_id) REFERENCES events(organization_id,id) ON DELETE RESTRICT,
  FOREIGN KEY (template_id) REFERENCES communication_templates(id) ON DELETE RESTRICT,
  FOREIGN KEY (recipient_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  UNIQUE (organization_id,event_id,deterministic_key),
  UNIQUE (organization_id,event_id,id)
);

CREATE TABLE communication_delivery_attempts (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  message_id TEXT NOT NULL,
  attempt_number INTEGER NOT NULL CHECK(attempt_number >= 1),
  status TEXT NOT NULL CHECK(status IN ('started','delivered','retryable_failure','permanent_failure')),
  provider_message_id TEXT,
  error_code TEXT,
  started_at_ms INTEGER NOT NULL,
  completed_at_ms INTEGER,
  FOREIGN KEY (organization_id,event_id,message_id)
    REFERENCES communication_messages(organization_id,event_id,id) ON DELETE RESTRICT,
  UNIQUE (message_id,attempt_number)
);

CREATE TABLE calendar_invitations (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  agenda_item_id TEXT NOT NULL,
  recipient_user_id TEXT NOT NULL,
  calendar_uid TEXT NOT NULL,
  sequence INTEGER NOT NULL DEFAULT 0 CHECK(sequence >= 0),
  last_content_hash BLOB,
  updated_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id,event_id) REFERENCES events(organization_id,id) ON DELETE RESTRICT,
  FOREIGN KEY (recipient_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  UNIQUE (organization_id,event_id,agenda_item_id,recipient_user_id),
  UNIQUE (calendar_uid)
);

CREATE TABLE reminder_schedules (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  template_id TEXT NOT NULL,
  recipient_user_id TEXT NOT NULL,
  send_at_ms INTEGER NOT NULL,
  schedule_version INTEGER NOT NULL DEFAULT 1 CHECK(schedule_version >= 1),
  state TEXT NOT NULL CHECK(state IN ('scheduled','dispatched','cancelled')),
  workflow_instance_id TEXT,
  dispatched_at_ms INTEGER,
  cancelled_at_ms INTEGER,
  updated_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id,event_id) REFERENCES events(organization_id,id) ON DELETE RESTRICT,
  FOREIGN KEY (template_id) REFERENCES communication_templates(id) ON DELETE RESTRICT,
  FOREIGN KEY (recipient_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  UNIQUE (organization_id,event_id,task_id,template_id),
  UNIQUE (organization_id,event_id,id)
);

CREATE INDEX idx_messages_delivery ON communication_messages(status,queued_at_ms,id);
CREATE INDEX idx_attempts_message ON communication_delivery_attempts(organization_id,event_id,message_id,attempt_number);
CREATE INDEX idx_reminders_due ON reminder_schedules(state,send_at_ms,id);
CREATE INDEX idx_messages_admin ON communication_messages(organization_id,event_id,updated_at_ms DESC,id DESC);
