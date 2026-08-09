PRAGMA foreign_keys = ON;

-- Stable event-scoped reads used by the application shell and CFP workspace.
CREATE INDEX idx_events_org_starts
  ON events(organization_id,starts_at_ms DESC,id DESC);
CREATE INDEX idx_programs_event_current
  ON programs(organization_id,event_id,updated_at_ms DESC,id DESC)
  WHERE status != 'archived';
CREATE UNIQUE INDEX uq_programs_event_current
  ON programs(organization_id,event_id) WHERE status != 'archived';
CREATE INDEX idx_cfp_forms_program_published
  ON call_for_speaker_forms(
    organization_id,event_id,program_id,version DESC,published_at_ms DESC,id DESC
  ) WHERE status = 'published';
CREATE INDEX idx_identity_invitations_event_recent
  ON identity_invitations(organization_id,event_id,created_at_ms DESC,id DESC);
CREATE INDEX idx_event_memberships_event_status
  ON event_memberships(organization_id,event_id,status,role,user_id);
CREATE INDEX idx_submission_drafts_scope
  ON submission_drafts(organization_id,event_id,program_id,updated_at_ms DESC,id DESC);
CREATE INDEX idx_evaluation_assignments_round_status
  ON evaluation_assignments(round_id,status,submission_id,evaluator_user_id,id);
CREATE UNIQUE INDEX uq_evaluation_rounds_program_open
  ON evaluation_rounds(organization_id,event_id,program_id) WHERE status='open';
CREATE INDEX idx_submission_decisions_submission
  ON submission_decisions(organization_id,event_id,submission_id,decided_at_ms DESC,id DESC);
CREATE UNIQUE INDEX uq_submission_decisions_final
  ON submission_decisions(organization_id,event_id,submission_id);
CREATE INDEX idx_communication_templates_event_kind
  ON communication_templates(organization_id,event_id,kind,updated_at_ms DESC,id DESC);
CREATE INDEX idx_calendar_invitations_agenda
  ON calendar_invitations(organization_id,event_id,agenda_item_id,recipient_user_id);
CREATE INDEX idx_reminder_schedules_task
  ON reminder_schedules(organization_id,event_id,task_id,state,send_at_ms,id);

CREATE TRIGGER validate_event_time_range_insert
BEFORE INSERT ON events
WHEN NEW.ends_at_ms <= NEW.starts_at_ms
BEGIN
  SELECT RAISE(ABORT, 'event end must be after start');
END;

CREATE TRIGGER validate_event_time_range_update
BEFORE UPDATE OF starts_at_ms,ends_at_ms ON events
WHEN NEW.ends_at_ms <= NEW.starts_at_ms
BEGIN
  SELECT RAISE(ABORT, 'event end must be after start');
END;

CREATE TRIGGER validate_submission_form_scope_insert
BEFORE INSERT ON submissions
WHEN NOT EXISTS (
  SELECT 1 FROM call_for_speaker_forms f
  WHERE f.id=NEW.form_id AND f.organization_id=NEW.organization_id
    AND f.event_id=NEW.event_id AND f.program_id=NEW.program_id
)
BEGIN
  SELECT RAISE(ABORT, 'submission form scope mismatch');
END;

CREATE TRIGGER validate_submission_form_scope_update
BEFORE UPDATE OF organization_id,event_id,program_id,form_id ON submissions
WHEN NOT EXISTS (
  SELECT 1 FROM call_for_speaker_forms f
  WHERE f.id=NEW.form_id AND f.organization_id=NEW.organization_id
    AND f.event_id=NEW.event_id AND f.program_id=NEW.program_id
)
BEGIN
  SELECT RAISE(ABORT, 'submission form scope mismatch');
END;

CREATE TRIGGER validate_submission_draft_scope_insert
BEFORE INSERT ON submission_drafts
WHEN NOT EXISTS (
  SELECT 1 FROM call_for_speaker_forms f
  WHERE f.id=NEW.form_id AND f.organization_id=NEW.organization_id
    AND f.event_id=NEW.event_id AND f.program_id=NEW.program_id
)
BEGIN
  SELECT RAISE(ABORT, 'submission draft scope mismatch');
END;

CREATE TRIGGER validate_submission_draft_scope_update
BEFORE UPDATE OF organization_id,event_id,program_id,form_id ON submission_drafts
WHEN NOT EXISTS (
  SELECT 1 FROM call_for_speaker_forms f
  WHERE f.id=NEW.form_id AND f.organization_id=NEW.organization_id
    AND f.event_id=NEW.event_id AND f.program_id=NEW.program_id
)
BEGIN
  SELECT RAISE(ABORT, 'submission draft scope mismatch');
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
    AND s.event_id=NEW.event_id AND s.program_id=r.program_id
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
    AND s.event_id=NEW.event_id AND s.program_id=r.program_id
)
BEGIN
  SELECT RAISE(ABORT, 'evaluation assignment scope mismatch');
END;

CREATE TRIGGER validate_evaluation_scope_insert
BEFORE INSERT ON evaluations
WHEN NOT EXISTS (
  SELECT 1 FROM evaluation_assignments a
  WHERE a.id=NEW.assignment_id AND a.round_id=NEW.round_id
    AND a.organization_id=NEW.organization_id AND a.event_id=NEW.event_id
    AND a.evaluator_user_id=NEW.evaluator_user_id
)
BEGIN
  SELECT RAISE(ABORT, 'evaluation scope mismatch');
END;

CREATE TRIGGER validate_evaluation_scope_update
BEFORE UPDATE OF organization_id,event_id,round_id,assignment_id,evaluator_user_id
ON evaluations
WHEN NOT EXISTS (
  SELECT 1 FROM evaluation_assignments a
  WHERE a.id=NEW.assignment_id AND a.round_id=NEW.round_id
    AND a.organization_id=NEW.organization_id AND a.event_id=NEW.event_id
    AND a.evaluator_user_id=NEW.evaluator_user_id
)
BEGIN
  SELECT RAISE(ABORT, 'evaluation scope mismatch');
END;

CREATE TRIGGER validate_submission_decision_scope_insert
BEFORE INSERT ON submission_decisions
WHEN NOT EXISTS (
  SELECT 1 FROM evaluation_rounds r
  JOIN submissions s ON s.id=NEW.submission_id
  WHERE r.id=NEW.round_id AND r.organization_id=NEW.organization_id
    AND r.event_id=NEW.event_id AND s.organization_id=NEW.organization_id
    AND s.event_id=NEW.event_id AND s.program_id=r.program_id
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
    AND s.event_id=NEW.event_id AND s.program_id=r.program_id
)
BEGIN
  SELECT RAISE(ABORT, 'submission decision scope mismatch');
END;

CREATE TRIGGER validate_evaluation_conflict_scope_insert
BEFORE INSERT ON evaluation_conflicts
WHEN NOT EXISTS (
  SELECT 1 FROM evaluation_assignments a
  WHERE a.id=NEW.assignment_id AND a.round_id=NEW.round_id
    AND a.organization_id=NEW.organization_id AND a.event_id=NEW.event_id
    AND a.evaluator_user_id=NEW.evaluator_user_id
)
BEGIN
  SELECT RAISE(ABORT, 'evaluation conflict scope mismatch');
END;

CREATE TRIGGER validate_evaluation_conflict_scope_update
BEFORE UPDATE OF organization_id,event_id,round_id,assignment_id,evaluator_user_id
ON evaluation_conflicts
WHEN NOT EXISTS (
  SELECT 1 FROM evaluation_assignments a
  WHERE a.id=NEW.assignment_id AND a.round_id=NEW.round_id
    AND a.organization_id=NEW.organization_id AND a.event_id=NEW.event_id
    AND a.evaluator_user_id=NEW.evaluator_user_id
)
BEGIN
  SELECT RAISE(ABORT, 'evaluation conflict scope mismatch');
END;

CREATE TRIGGER validate_speaker_task_submission_owner_insert
BEFORE INSERT ON speaker_tasks
WHEN NEW.submission_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM submission_speakers ss
  WHERE ss.organization_id=NEW.organization_id AND ss.event_id=NEW.event_id
    AND ss.submission_id=NEW.submission_id AND ss.event_speaker_id=NEW.event_speaker_id
)
BEGIN
  SELECT RAISE(ABORT, 'speaker task submission owner mismatch');
END;

CREATE TRIGGER validate_speaker_task_submission_owner_update
BEFORE UPDATE OF organization_id,event_id,event_speaker_id,submission_id ON speaker_tasks
WHEN NEW.submission_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM submission_speakers ss
  WHERE ss.organization_id=NEW.organization_id AND ss.event_id=NEW.event_id
    AND ss.submission_id=NEW.submission_id AND ss.event_speaker_id=NEW.event_speaker_id
)
BEGIN
  SELECT RAISE(ABORT, 'speaker task submission owner mismatch');
END;

CREATE TRIGGER validate_communication_message_scope_insert
BEFORE INSERT ON communication_messages
WHEN NEW.template_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM communication_templates t
  WHERE t.id=NEW.template_id AND t.organization_id=NEW.organization_id
    AND t.event_id=NEW.event_id
)
BEGIN
  SELECT RAISE(ABORT, 'communication template scope mismatch');
END;

CREATE TRIGGER validate_communication_message_scope_update
BEFORE UPDATE OF organization_id,event_id,template_id ON communication_messages
WHEN NEW.template_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM communication_templates t
  WHERE t.id=NEW.template_id AND t.organization_id=NEW.organization_id
    AND t.event_id=NEW.event_id
)
BEGIN
  SELECT RAISE(ABORT, 'communication template scope mismatch');
END;

CREATE TRIGGER validate_calendar_invitation_agenda_insert
BEFORE INSERT ON calendar_invitations
WHEN NOT EXISTS (
  SELECT 1 FROM agenda_items a
  WHERE a.id=NEW.agenda_item_id AND a.organization_id=NEW.organization_id
    AND a.event_id=NEW.event_id
)
BEGIN
  SELECT RAISE(ABORT, 'calendar invitation agenda scope mismatch');
END;

CREATE TRIGGER validate_calendar_invitation_agenda_update
BEFORE UPDATE OF organization_id,event_id,agenda_item_id ON calendar_invitations
WHEN NOT EXISTS (
  SELECT 1 FROM agenda_items a
  WHERE a.id=NEW.agenda_item_id AND a.organization_id=NEW.organization_id
    AND a.event_id=NEW.event_id
)
BEGIN
  SELECT RAISE(ABORT, 'calendar invitation agenda scope mismatch');
END;

CREATE TRIGGER validate_reminder_scope_insert
BEFORE INSERT ON reminder_schedules
WHEN NOT EXISTS (
  SELECT 1 FROM speaker_tasks s
  JOIN communication_templates t ON t.id=NEW.template_id
  WHERE s.id=NEW.task_id AND s.organization_id=NEW.organization_id
    AND s.event_id=NEW.event_id AND t.organization_id=NEW.organization_id
    AND t.event_id=NEW.event_id
)
BEGIN
  SELECT RAISE(ABORT, 'reminder scope mismatch');
END;

CREATE TRIGGER validate_reminder_scope_update
BEFORE UPDATE OF organization_id,event_id,task_id,template_id ON reminder_schedules
WHEN NOT EXISTS (
  SELECT 1 FROM speaker_tasks s
  JOIN communication_templates t ON t.id=NEW.template_id
  WHERE s.id=NEW.task_id AND s.organization_id=NEW.organization_id
    AND s.event_id=NEW.event_id AND t.organization_id=NEW.organization_id
    AND t.event_id=NEW.event_id
)
BEGIN
  SELECT RAISE(ABORT, 'reminder scope mismatch');
END;

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

CREATE TRIGGER validate_auth_challenge_scope_insert
BEFORE INSERT ON authentication_challenges
WHEN (NEW.event_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM events e
        WHERE e.id=NEW.event_id AND e.organization_id=NEW.organization_id
      ))
  OR (NEW.user_id IS NOT NULL AND NEW.organization_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM organization_memberships m
        WHERE m.organization_id=NEW.organization_id AND m.user_id=NEW.user_id
          AND m.status='active'
      ))
  OR (NEW.invitation_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM identity_invitations i
        WHERE i.id=NEW.invitation_id AND i.organization_id=NEW.organization_id
          AND i.event_id=NEW.event_id AND i.normalized_email=NEW.normalized_email
      ))
BEGIN
  SELECT RAISE(ABORT, 'authentication challenge scope mismatch');
END;

CREATE TRIGGER validate_auth_challenge_scope_update
BEFORE UPDATE OF user_id,organization_id,event_id,invitation_id,normalized_email
ON authentication_challenges
WHEN (NEW.event_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM events e
        WHERE e.id=NEW.event_id AND e.organization_id=NEW.organization_id
      ))
  OR (NEW.user_id IS NOT NULL AND NEW.organization_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM organization_memberships m
        WHERE m.organization_id=NEW.organization_id AND m.user_id=NEW.user_id
          AND m.status='active'
      ))
  OR (NEW.invitation_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM identity_invitations i
        WHERE i.id=NEW.invitation_id AND i.organization_id=NEW.organization_id
          AND i.event_id=NEW.event_id AND i.normalized_email=NEW.normalized_email
      ))
BEGIN
  SELECT RAISE(ABORT, 'authentication challenge scope mismatch');
END;

CREATE TRIGGER validate_auth_challenge_time_insert
BEFORE INSERT ON authentication_challenges
WHEN NEW.expires_at_ms <= NEW.created_at_ms
  OR (NEW.consumed_at_ms IS NOT NULL AND NEW.consumed_at_ms < NEW.created_at_ms)
BEGIN
  SELECT RAISE(ABORT, 'authentication challenge time range invalid');
END;

CREATE TRIGGER validate_auth_challenge_time_update
BEFORE UPDATE OF created_at_ms,expires_at_ms,consumed_at_ms ON authentication_challenges
WHEN NEW.expires_at_ms <= NEW.created_at_ms
  OR (NEW.consumed_at_ms IS NOT NULL AND NEW.consumed_at_ms < NEW.created_at_ms)
BEGIN
  SELECT RAISE(ABORT, 'authentication challenge time range invalid');
END;

CREATE TRIGGER validate_identity_invitation_actor_insert
BEFORE INSERT ON identity_invitations
WHEN NOT EXISTS (
  SELECT 1 FROM organization_memberships m
  WHERE m.organization_id=NEW.organization_id AND m.user_id=NEW.invited_by_user_id
    AND m.status='active'
)
BEGIN
  SELECT RAISE(ABORT, 'invitation actor scope mismatch');
END;

CREATE TRIGGER validate_identity_invitation_actor_update
BEFORE UPDATE OF organization_id,invited_by_user_id ON identity_invitations
WHEN NOT EXISTS (
  SELECT 1 FROM organization_memberships m
  WHERE m.organization_id=NEW.organization_id AND m.user_id=NEW.invited_by_user_id
    AND m.status='active'
)
BEGIN
  SELECT RAISE(ABORT, 'invitation actor scope mismatch');
END;

CREATE TRIGGER validate_identity_invitation_state_insert
BEFORE INSERT ON identity_invitations
WHEN NEW.expires_at_ms <= NEW.created_at_ms
  OR ((NEW.status='accepted') != (NEW.accepted_at_ms IS NOT NULL))
  OR ((NEW.status='revoked') != (NEW.revoked_at_ms IS NOT NULL))
BEGIN
  SELECT RAISE(ABORT, 'invitation state invalid');
END;

CREATE TRIGGER validate_identity_invitation_state_update
BEFORE UPDATE OF status,expires_at_ms,accepted_at_ms,revoked_at_ms,created_at_ms
ON identity_invitations
WHEN NEW.expires_at_ms <= NEW.created_at_ms
  OR ((NEW.status='accepted') != (NEW.accepted_at_ms IS NOT NULL))
  OR ((NEW.status='revoked') != (NEW.revoked_at_ms IS NOT NULL))
BEGIN
  SELECT RAISE(ABORT, 'invitation state invalid');
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

CREATE TRIGGER validate_submission_decision_actor_insert
BEFORE INSERT ON submission_decisions
WHEN NOT EXISTS (
  SELECT 1 FROM organization_memberships m
  WHERE m.organization_id=NEW.organization_id AND m.user_id=NEW.decided_by_user_id
    AND m.status='active'
)
BEGIN
  SELECT RAISE(ABORT, 'decision actor scope mismatch');
END;

CREATE TRIGGER validate_submission_decision_actor_update
BEFORE UPDATE OF organization_id,decided_by_user_id ON submission_decisions
WHEN NOT EXISTS (
  SELECT 1 FROM organization_memberships m
  WHERE m.organization_id=NEW.organization_id AND m.user_id=NEW.decided_by_user_id
    AND m.status='active'
)
BEGIN
  SELECT RAISE(ABORT, 'decision actor scope mismatch');
END;

CREATE TRIGGER validate_schedule_revision_actor_insert
BEFORE INSERT ON schedule_revisions
WHEN NOT EXISTS (
  SELECT 1 FROM organization_memberships m
  WHERE m.organization_id=NEW.organization_id AND m.user_id=NEW.created_by_user_id
    AND m.status='active'
)
BEGIN
  SELECT RAISE(ABORT, 'schedule creator scope mismatch');
END;

CREATE TRIGGER validate_schedule_revision_actor_update
BEFORE UPDATE OF organization_id,created_by_user_id ON schedule_revisions
WHEN NOT EXISTS (
  SELECT 1 FROM organization_memberships m
  WHERE m.organization_id=NEW.organization_id AND m.user_id=NEW.created_by_user_id
    AND m.status='active'
)
BEGIN
  SELECT RAISE(ABORT, 'schedule creator scope mismatch');
END;

CREATE TRIGGER validate_event_resource_actor_insert
BEFORE INSERT ON event_resources
WHEN NOT EXISTS (
  SELECT 1 FROM organization_memberships m
  WHERE m.organization_id=NEW.organization_id AND m.user_id=NEW.created_by_user_id
    AND m.status='active'
)
BEGIN
  SELECT RAISE(ABORT, 'resource creator scope mismatch');
END;

CREATE TRIGGER validate_event_resource_actor_update
BEFORE UPDATE OF organization_id,created_by_user_id ON event_resources
WHEN NOT EXISTS (
  SELECT 1 FROM organization_memberships m
  WHERE m.organization_id=NEW.organization_id AND m.user_id=NEW.created_by_user_id
    AND m.status='active'
)
BEGIN
  SELECT RAISE(ABORT, 'resource creator scope mismatch');
END;

CREATE TRIGGER validate_integration_token_actor_insert
BEFORE INSERT ON event_integration_tokens
WHEN NOT EXISTS (
  SELECT 1 FROM organization_memberships m
  WHERE m.organization_id=NEW.organization_id AND m.user_id=NEW.created_by_user_id
    AND m.status='active'
)
BEGIN
  SELECT RAISE(ABORT, 'integration token creator scope mismatch');
END;

CREATE TRIGGER validate_integration_token_actor_update
BEFORE UPDATE OF organization_id,created_by_user_id ON event_integration_tokens
WHEN NOT EXISTS (
  SELECT 1 FROM organization_memberships m
  WHERE m.organization_id=NEW.organization_id AND m.user_id=NEW.created_by_user_id
    AND m.status='active'
)
BEGIN
  SELECT RAISE(ABORT, 'integration token creator scope mismatch');
END;

CREATE TRIGGER validate_communication_recipient_insert
BEFORE INSERT ON communication_messages
WHEN NEW.recipient_user_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM organization_memberships m
  WHERE m.organization_id=NEW.organization_id AND m.user_id=NEW.recipient_user_id
    AND m.status='active'
)
BEGIN
  SELECT RAISE(ABORT, 'communication recipient scope mismatch');
END;

CREATE TRIGGER validate_communication_recipient_update
BEFORE UPDATE OF organization_id,recipient_user_id ON communication_messages
WHEN NEW.recipient_user_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM organization_memberships m
  WHERE m.organization_id=NEW.organization_id AND m.user_id=NEW.recipient_user_id
    AND m.status='active'
)
BEGIN
  SELECT RAISE(ABORT, 'communication recipient scope mismatch');
END;

CREATE TRIGGER validate_calendar_invitation_recipient_insert
BEFORE INSERT ON calendar_invitations
WHEN NOT EXISTS (
  SELECT 1 FROM event_memberships m
  WHERE m.organization_id=NEW.organization_id AND m.event_id=NEW.event_id
    AND m.user_id=NEW.recipient_user_id AND m.status='active'
)
BEGIN
  SELECT RAISE(ABORT, 'calendar invitation recipient scope mismatch');
END;

CREATE TRIGGER validate_calendar_invitation_recipient_update
BEFORE UPDATE OF organization_id,event_id,recipient_user_id ON calendar_invitations
WHEN NOT EXISTS (
  SELECT 1 FROM event_memberships m
  WHERE m.organization_id=NEW.organization_id AND m.event_id=NEW.event_id
    AND m.user_id=NEW.recipient_user_id AND m.status='active'
)
BEGIN
  SELECT RAISE(ABORT, 'calendar invitation recipient scope mismatch');
END;
