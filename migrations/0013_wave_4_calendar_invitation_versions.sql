CREATE UNIQUE INDEX IF NOT EXISTS uq_calendar_invitation_tenant_id
  ON calendar_invitations(organization_id,event_id,id);

CREATE TABLE calendar_invitation_versions (
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
    REFERENCES communication_messages(organization_id,event_id,id) ON DELETE RESTRICT,
  UNIQUE (invitation_id,sequence),
  UNIQUE (communication_message_id)
);

CREATE INDEX idx_calendar_versions_delivery
  ON calendar_invitation_versions(organization_id,event_id,communication_message_id);
