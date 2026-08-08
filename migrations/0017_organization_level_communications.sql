PRAGMA defer_foreign_keys = on;

CREATE TABLE communication_messages_rebuild (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT,
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

INSERT INTO communication_messages_rebuild
  (id,organization_id,event_id,template_id,recipient_user_id,recipient_email,subject,
   html_body,deterministic_key,status,provider_message_id,last_error_code,attempt_count,
   queued_at_ms,delivered_at_ms,updated_at_ms)
SELECT id,organization_id,event_id,template_id,recipient_user_id,recipient_email,subject,
       html_body,deterministic_key,status,provider_message_id,last_error_code,attempt_count,
       queued_at_ms,delivered_at_ms,updated_at_ms
FROM communication_messages;

CREATE TABLE communication_delivery_attempts_rebuild (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT,
  message_id TEXT NOT NULL,
  attempt_number INTEGER NOT NULL CHECK(attempt_number >= 1),
  status TEXT NOT NULL CHECK(status IN ('started','delivered','retryable_failure','permanent_failure')),
  provider_message_id TEXT,
  error_code TEXT,
  started_at_ms INTEGER NOT NULL,
  completed_at_ms INTEGER,
  FOREIGN KEY (organization_id,event_id,message_id)
    REFERENCES communication_messages_rebuild(organization_id,event_id,id) ON DELETE RESTRICT,
  FOREIGN KEY (message_id) REFERENCES communication_messages_rebuild(id) ON DELETE RESTRICT,
  UNIQUE (message_id,attempt_number)
);

INSERT INTO communication_delivery_attempts_rebuild
  (id,organization_id,event_id,message_id,attempt_number,status,provider_message_id,
   error_code,started_at_ms,completed_at_ms)
SELECT id,organization_id,event_id,message_id,attempt_number,status,provider_message_id,
       error_code,started_at_ms,completed_at_ms
FROM communication_delivery_attempts;

CREATE TABLE calendar_invitation_versions_rebuild (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  invitation_id TEXT NOT NULL,
  sequence INTEGER NOT NULL CHECK(sequence >= 0),
  content_hash BLOB NOT NULL,
  ics_content TEXT NOT NULL,
  communication_message_id TEXT NOT NULL,
  created_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id,event_id,invitation_id)
    REFERENCES calendar_invitations(organization_id,event_id,id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id,event_id,communication_message_id)
    REFERENCES communication_messages_rebuild(organization_id,event_id,id) ON DELETE RESTRICT,
  UNIQUE (invitation_id,sequence),
  UNIQUE (communication_message_id)
);

INSERT INTO calendar_invitation_versions_rebuild
  (id,organization_id,event_id,invitation_id,sequence,content_hash,ics_content,
   communication_message_id,created_at_ms)
SELECT id,organization_id,event_id,invitation_id,sequence,content_hash,ics_content,
       communication_message_id,created_at_ms
FROM calendar_invitation_versions;

DROP TABLE calendar_invitation_versions;
DROP TABLE communication_delivery_attempts;
DROP TABLE communication_messages;

ALTER TABLE communication_messages_rebuild RENAME TO communication_messages;
ALTER TABLE communication_delivery_attempts_rebuild RENAME TO communication_delivery_attempts;
ALTER TABLE calendar_invitation_versions_rebuild RENAME TO calendar_invitation_versions;

CREATE INDEX idx_messages_delivery ON communication_messages(status,queued_at_ms,id);
CREATE INDEX idx_messages_admin
  ON communication_messages(organization_id,event_id,updated_at_ms DESC,id DESC);
CREATE UNIQUE INDEX uq_messages_organization_key_without_event
  ON communication_messages(organization_id,deterministic_key) WHERE event_id IS NULL;
CREATE INDEX idx_attempts_message
  ON communication_delivery_attempts(organization_id,event_id,message_id,attempt_number);
CREATE INDEX idx_calendar_versions_delivery
  ON calendar_invitation_versions(organization_id,event_id,communication_message_id);

PRAGMA defer_foreign_keys = off;
