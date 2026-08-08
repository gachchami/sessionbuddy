-- Compatibility repair for environments that applied the first Wave 4
-- calendar migration before the composite parent index was included.
CREATE UNIQUE INDEX IF NOT EXISTS uq_calendar_invitation_tenant_id
  ON calendar_invitations(organization_id,event_id,id);
