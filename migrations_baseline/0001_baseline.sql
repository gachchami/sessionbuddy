-- Canonical SessionBuddy fresh-install schema.
-- This is the sole active D1 migration; existing databases are recreated when it changes.

PRAGMA foreign_keys = ON;

CREATE TABLE accepted_session_labels (
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  accepted_session_id TEXT NOT NULL,
  label_id TEXT NOT NULL,
  assigned_by_user_id TEXT NOT NULL,
  created_at_ms INTEGER NOT NULL,
  PRIMARY KEY (accepted_session_id,label_id),
  FOREIGN KEY (organization_id,event_id,accepted_session_id)
    REFERENCES accepted_sessions(organization_id,event_id,id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id,event_id,label_id)
    REFERENCES event_labels(organization_id,event_id,id) ON DELETE RESTRICT,
  FOREIGN KEY (assigned_by_user_id) REFERENCES users(id) ON DELETE RESTRICT
);

CREATE TABLE accepted_sessions (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  submission_id TEXT NOT NULL,
  decision_id TEXT NOT NULL,
  created_at_ms INTEGER NOT NULL, content_status TEXT NOT NULL DEFAULT 'draft'
  CHECK (content_status IN ('draft', 'approved')), version INTEGER NOT NULL DEFAULT 1
  CHECK (version >= 1), label_version INTEGER NOT NULL DEFAULT 1 CHECK(label_version >= 1),
  FOREIGN KEY (organization_id, event_id, submission_id)
    REFERENCES submissions(organization_id, event_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (decision_id) REFERENCES submission_decisions(id) ON DELETE RESTRICT,
  UNIQUE (organization_id, event_id, id),
  UNIQUE (organization_id, event_id, submission_id),
  UNIQUE (decision_id)
);

CREATE TABLE agenda_item_speakers (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  agenda_item_id TEXT NOT NULL,
  event_speaker_id TEXT NOT NULL,
  created_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id, event_id, revision_id, agenda_item_id)
    REFERENCES agenda_items(organization_id, event_id, revision_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, event_id, event_speaker_id)
    REFERENCES event_speakers(organization_id, event_id, id) ON DELETE RESTRICT,
  UNIQUE (revision_id, agenda_item_id, event_speaker_id)
);

CREATE TABLE agenda_items (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  accepted_session_id TEXT NOT NULL,
  room_id TEXT NOT NULL,
  track_id TEXT,
  event_date TEXT NOT NULL CHECK (
    length(event_date)=10 AND event_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
  ),
  event_time_zone TEXT NOT NULL,
  starts_at_ms INTEGER NOT NULL,
  ends_at_ms INTEGER NOT NULL,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id, event_id, revision_id)
    REFERENCES schedule_revisions(organization_id, event_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, event_id, accepted_session_id)
    REFERENCES accepted_sessions(organization_id, event_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, event_id, room_id)
    REFERENCES event_rooms(organization_id, event_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, event_id, track_id)
    REFERENCES event_tracks(organization_id, event_id, id) ON DELETE RESTRICT,
  UNIQUE (organization_id, event_id, revision_id, id),
  UNIQUE (revision_id, accepted_session_id),
  CHECK (starts_at_ms < ends_at_ms)
);

CREATE TABLE agenda_write_guards (
  id TEXT PRIMARY KEY NOT NULL,
  agenda_item_id TEXT NOT NULL,
  applied_changes INTEGER NOT NULL CHECK (applied_changes = 1),
  created_at_ms INTEGER NOT NULL
);

CREATE TABLE "ai_triage_results" (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  submission_id TEXT NOT NULL,
  model TEXT NOT NULL CHECK(length(model) BETWEEN 1 AND 200),
  score INTEGER NOT NULL CHECK(score BETWEEN 0 AND 10),
  recommendation TEXT NOT NULL CHECK(length(recommendation) BETWEEN 1 AND 80),
  rationale TEXT NOT NULL CHECK(length(rationale) BETWEEN 1 AND 4000),
  generated_by_user_id TEXT NOT NULL,
  generated_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id,event_id,submission_id)
    REFERENCES "submissions"(organization_id,event_id,id) ON DELETE CASCADE,
  FOREIGN KEY (generated_by_user_id) REFERENCES users(id)
);

CREATE TABLE asset_download_grants (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  principal_user_id TEXT NOT NULL,
  asset_version_id TEXT NOT NULL,
  purpose TEXT NOT NULL CHECK (purpose IN ('speaker_download', 'admin_download')),
  token_hash BLOB NOT NULL CHECK (length(token_hash) = 32),
  expires_at_ms INTEGER NOT NULL,
  consumed_at_ms INTEGER,
  created_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id, principal_user_id)
    REFERENCES organization_memberships(organization_id, user_id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, event_id, asset_version_id)
    REFERENCES speaker_asset_versions(organization_id, event_id, id) ON DELETE RESTRICT,
  UNIQUE (token_hash),
  CHECK (expires_at_ms > created_at_ms),
  CHECK (consumed_at_ms IS NULL OR consumed_at_ms >= created_at_ms)
);

CREATE TABLE asset_scan_events (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  asset_version_id TEXT NOT NULL,
  generation INTEGER NOT NULL CHECK (generation >= 1),
  checksum_sha256 BLOB NOT NULL CHECK (length(checksum_sha256) = 32),
  provider_event_id TEXT NOT NULL CHECK (length(provider_event_id) BETWEEN 1 AND 200),
  job_id TEXT NOT NULL CHECK (length(job_id) BETWEEN 1 AND 200),
  verdict TEXT NOT NULL CHECK (verdict IN ('clean', 'malicious', 'error')),
  engine TEXT NOT NULL CHECK (
    length(engine) BETWEEN 1 AND 80
    AND engine NOT GLOB '*[^A-Za-z0-9._:-]*'
  ),
  signature_code TEXT CHECK (
    signature_code IS NULL
    OR (
      length(signature_code) BETWEEN 1 AND 100
      AND signature_code NOT GLOB '*[^A-Za-z0-9._:-]*'
    )
  ),
  received_at_ms INTEGER NOT NULL CHECK (received_at_ms >= 0),
  FOREIGN KEY (
    organization_id,
    event_id,
    asset_version_id,
    generation,
    checksum_sha256
  ) REFERENCES speaker_asset_versions(
    organization_id,
    event_id,
    id,
    generation,
    checksum_sha256
  ) ON DELETE RESTRICT,
  UNIQUE (engine, provider_event_id),
  UNIQUE (engine, job_id, asset_version_id, generation, checksum_sha256)
);

CREATE TABLE audit_events (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT,
  event_id TEXT,
  actor_user_id TEXT,
  actor_type TEXT NOT NULL CHECK (actor_type IN ('user', 'system', 'anonymous')),
  action TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id TEXT,
  result TEXT NOT NULL CHECK (result IN ('succeeded', 'denied', 'failed')),
  reason_code TEXT,
  correlation_id TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata_json)),
  occurred_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, event_id) REFERENCES events(organization_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (actor_user_id) REFERENCES users(id) ON DELETE RESTRICT
);

CREATE TABLE authentication_challenges (
  id TEXT PRIMARY KEY NOT NULL,
  normalized_email TEXT NOT NULL,
  token_hash BLOB NOT NULL,
  purpose TEXT NOT NULL CHECK (purpose IN ('sign_in', 'verify_email')),
  provisioning_context TEXT NOT NULL CHECK (provisioning_context IN ('invitation', 'submission', 'existing_user')),
  redirect_path TEXT NOT NULL,
  requested_ip_hash BLOB,
  expires_at_ms INTEGER NOT NULL,
  consumed_at_ms INTEGER,
  created_at_ms INTEGER NOT NULL, user_id TEXT REFERENCES users(id), organization_id TEXT REFERENCES organizations(id), event_id TEXT, invitation_id TEXT,
  UNIQUE (token_hash)
);

CREATE TABLE "calendar_invitation_versions" (
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
    REFERENCES "communication_messages"(organization_id,event_id,id) ON DELETE RESTRICT,
  UNIQUE (invitation_id,sequence),
  UNIQUE (communication_message_id)
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

CREATE TABLE "call_for_speaker_forms" (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  version INTEGER NOT NULL CHECK (version >= 1),
  slug TEXT NOT NULL CHECK (length(slug) BETWEEN 3 AND 80),
  welcome_text TEXT NOT NULL CHECK (length(welcome_text) BETWEEN 1 AND 1000),
  schema_json TEXT NOT NULL CHECK (json_valid(schema_json)),
  status TEXT NOT NULL CHECK (status IN ('draft', 'published', 'closed')),
  published_at_ms INTEGER,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  opens_at_ms INTEGER,
  closes_at_ms INTEGER,
  submission_limit INTEGER
    CHECK (submission_limit IS NULL OR submission_limit BETWEEN 1 AND 1000000),
  success_title TEXT NOT NULL DEFAULT 'Proposal received'
    CHECK (length(success_title) BETWEEN 1 AND 200),
  success_message TEXT NOT NULL DEFAULT 'We sent a confirmation to your email address.'
    CHECK (length(success_message) BETWEEN 1 AND 2000),
  redirect_to_portal INTEGER NOT NULL DEFAULT 1 CHECK (redirect_to_portal IN (0, 1)),
  confirmation_subject TEXT NOT NULL DEFAULT 'We received your proposal'
    CHECK (length(confirmation_subject) BETWEEN 1 AND 200),
  confirmation_body TEXT NOT NULL
    DEFAULT 'Thank you for submitting. Your proposal is now ready for review.'
    CHECK (length(confirmation_body) BETWEEN 1 AND 4000),
  FOREIGN KEY (organization_id,event_id)
    REFERENCES events(organization_id,id) ON DELETE RESTRICT,
  UNIQUE (slug),
  UNIQUE (organization_id,event_id,version),
  CHECK ((status = 'published') = (published_at_ms IS NOT NULL))
);

CREATE TABLE cfp_form_write_guards (
  id TEXT PRIMARY KEY NOT NULL,
  form_id TEXT NOT NULL,
  applied_changes INTEGER NOT NULL CHECK (applied_changes = 1),
  created_at_ms INTEGER NOT NULL,
  FOREIGN KEY (form_id) REFERENCES call_for_speaker_forms(id) ON DELETE RESTRICT
);

CREATE TABLE cfp_staged_assets (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  form_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('headshot', 'supporting_document')),
  object_key TEXT NOT NULL CHECK (length(object_key) BETWEEN 16 AND 1024),
  original_filename TEXT NOT NULL CHECK (length(original_filename) BETWEEN 1 AND 255),
  content_type TEXT NOT NULL CHECK (length(content_type) BETWEEN 1 AND 255),
  byte_size INTEGER NOT NULL CHECK (byte_size > 0),
  checksum_sha256 BLOB NOT NULL CHECK (length(checksum_sha256) = 32),
  upload_token_hash BLOB NOT NULL CHECK (length(upload_token_hash) = 32),
  status TEXT NOT NULL CHECK (
    status IN ('pending_upload', 'uploaded', 'scanning', 'staged', 'rejected', 'claimed')
  ),
  scan_result_code TEXT CHECK (scan_result_code IS NULL OR length(scan_result_code) <= 100),
  claimed_submission_id TEXT,
  claimed_at_ms INTEGER,
  expires_at_ms INTEGER NOT NULL,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  FOREIGN KEY (form_id) REFERENCES call_for_speaker_forms(id) ON DELETE RESTRICT,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT,
  UNIQUE (upload_token_hash),
  UNIQUE (object_key),
  CHECK (expires_at_ms > created_at_ms),
  CHECK (
    status <> 'claimed'
    OR (claimed_submission_id IS NOT NULL AND claimed_at_ms IS NOT NULL)
  ),
  CHECK (claimed_submission_id IS NULL OR status = 'claimed')
);

-- These guards close the authorization-quota race at the database boundary.
-- Application preflight checks provide the friendly 429 response; the
-- triggers are the final authority when concurrent requests pass preflight.
CREATE TRIGGER cfp_staged_assets_creation_quota
BEFORE INSERT ON cfp_staged_assets
WHEN (
  SELECT COUNT(*) FROM cfp_staged_assets existing
  WHERE existing.form_id=NEW.form_id AND existing.user_id=NEW.user_id
    AND existing.created_at_ms > NEW.created_at_ms - 3600000
) >= 20
BEGIN
  SELECT RAISE(ABORT, 'cfp staged authorization quota exceeded');
END;

CREATE TRIGGER cfp_staged_assets_active_quota
BEFORE INSERT ON cfp_staged_assets
WHEN (
  SELECT COUNT(*) FROM cfp_staged_assets existing
  WHERE existing.form_id=NEW.form_id AND existing.user_id=NEW.user_id
    AND existing.expires_at_ms > NEW.created_at_ms
    AND existing.status IN ('pending_upload','uploaded','scanning','staged')
) >= 10 OR (
  SELECT COALESCE(SUM(existing.byte_size), 0) FROM cfp_staged_assets existing
  WHERE existing.form_id=NEW.form_id AND existing.user_id=NEW.user_id
    AND existing.expires_at_ms > NEW.created_at_ms
    AND existing.status IN ('pending_upload','uploaded','scanning','staged')
) + NEW.byte_size > 104857600
BEGIN
  SELECT RAISE(ABORT, 'cfp staged active quota exceeded');
END;

CREATE TABLE "communication_delivery_attempts" (
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
    REFERENCES "communication_messages"(organization_id,event_id,id) ON DELETE RESTRICT,
  FOREIGN KEY (message_id) REFERENCES "communication_messages"(id) ON DELETE RESTRICT,
  UNIQUE (message_id,attempt_number)
);

CREATE TABLE "communication_messages" (
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
  updated_at_ms INTEGER NOT NULL, attempt_limit INTEGER NOT NULL DEFAULT 12
CHECK (attempt_limit >= 12),
  FOREIGN KEY (organization_id,event_id) REFERENCES events(organization_id,id) ON DELETE RESTRICT,
  FOREIGN KEY (template_id) REFERENCES communication_templates(id) ON DELETE RESTRICT,
  FOREIGN KEY (recipient_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  UNIQUE (organization_id,event_id,deterministic_key),
  UNIQUE (organization_id,event_id,id)
);

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

-- Round membership is explicit, and separate from the assignment relation between the
-- two memberships. Inferring membership from evaluation_assignments cannot represent a
-- selected proposal with no reviewer yet, or a reviewer in the pool with no proposals --
-- so the last removal silently dropped them from the round entirely.
-- Lifecycle is a status, not a deletion: revoked assignments stay as audit records and
-- reference their membership, so a physical delete would be blocked by ON DELETE RESTRICT
-- for exactly the rows whose history we most want to keep.
CREATE TABLE evaluation_round_submissions (
  round_id TEXT NOT NULL,
  submission_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active', 'removed')),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  PRIMARY KEY (round_id, submission_id),
  FOREIGN KEY (round_id) REFERENCES evaluation_rounds(id) ON DELETE RESTRICT,
  FOREIGN KEY (submission_id) REFERENCES submissions(id) ON DELETE RESTRICT
);

CREATE TABLE evaluation_round_evaluators (
  round_id TEXT NOT NULL,
  evaluator_user_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active', 'removed')),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  PRIMARY KEY (round_id, evaluator_user_id),
  FOREIGN KEY (round_id) REFERENCES evaluation_rounds(id) ON DELETE RESTRICT,
  FOREIGN KEY (evaluator_user_id) REFERENCES users(id) ON DELETE RESTRICT
);

CREATE TABLE evaluation_assignments (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  round_id TEXT NOT NULL,
  submission_id TEXT NOT NULL,
  evaluator_user_id TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'evaluator' CHECK (role = 'evaluator'),
  status TEXT NOT NULL CHECK (status IN ('assigned', 'completed', 'revoked')),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  FOREIGN KEY (round_id) REFERENCES evaluation_rounds(id) ON DELETE RESTRICT,
  -- Parents are the round memberships, not submissions/users directly: an assignment
  -- cannot exist for a proposal or reviewer that is not in the round. Note this enforces
  -- MEMBERSHIP, not ACTIVE membership -- "no assignment against a removed membership" is
  -- a status rule the FK cannot express, and is enforced in the round diff.
  FOREIGN KEY (round_id, submission_id)
    REFERENCES evaluation_round_submissions(round_id, submission_id) ON DELETE RESTRICT,
  FOREIGN KEY (round_id, evaluator_user_id)
    REFERENCES evaluation_round_evaluators(round_id, evaluator_user_id) ON DELETE RESTRICT,
  UNIQUE (round_id, submission_id, evaluator_user_id)
);

CREATE TABLE evaluation_conflicts (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  round_id TEXT NOT NULL,
  assignment_id TEXT NOT NULL,
  evaluator_user_id TEXT NOT NULL,
  conflict_type TEXT NOT NULL CHECK (conflict_type IN ('speaker_relationship', 'same_company', 'financial', 'other')),
  explanation TEXT NOT NULL CHECK (length(explanation) BETWEEN 1 AND 1000),
  declared_at_ms INTEGER NOT NULL,
  FOREIGN KEY (round_id) REFERENCES evaluation_rounds(id) ON DELETE RESTRICT,
  FOREIGN KEY (assignment_id) REFERENCES evaluation_assignments(id) ON DELETE RESTRICT,
  FOREIGN KEY (evaluator_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  UNIQUE (assignment_id)
);

CREATE TABLE "evaluation_rounds" (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  rubric_json TEXT NOT NULL CHECK (json_valid(rubric_json)),
  status TEXT NOT NULL CHECK (status IN ('draft', 'open', 'closed')),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  closed_at_ms INTEGER,
  review_opens_at_ms INTEGER,
  review_closes_at_ms INTEGER,
  FOREIGN KEY (organization_id,event_id)
    REFERENCES events(organization_id,id) ON DELETE RESTRICT,
  CHECK ((status = 'closed') = (closed_at_ms IS NOT NULL))
);

CREATE TABLE "evaluations" (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  round_id TEXT NOT NULL,
  assignment_id TEXT NOT NULL,
  evaluator_user_id TEXT NOT NULL,
  rating INTEGER CHECK (rating IS NULL OR rating BETWEEN 0 AND 10),
  recommendation TEXT CHECK (
    recommendation IS NULL OR length(recommendation) BETWEEN 1 AND 80
  ),
  internal_comment TEXT NOT NULL CHECK (length(internal_comment) <= 5000),
  state TEXT NOT NULL CHECK (state IN ('draft', 'final')),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  finalized_at_ms INTEGER,
  criterion_responses_json TEXT NOT NULL DEFAULT '{}'
  CHECK (json_valid(criterion_responses_json)),
  FOREIGN KEY (round_id) REFERENCES evaluation_rounds(id) ON DELETE RESTRICT,
  FOREIGN KEY (assignment_id) REFERENCES evaluation_assignments(id) ON DELETE RESTRICT,
  UNIQUE (assignment_id),
  CHECK ((state = 'final') = (finalized_at_ms IS NOT NULL)),
  CHECK (state != 'final' OR (rating IS NOT NULL AND recommendation IS NOT NULL))
);

CREATE TABLE event_branding_assets (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT,
  kind TEXT NOT NULL CHECK (kind IN ('logo', 'cover')),
  object_key TEXT NOT NULL CHECK (
    length(object_key) BETWEEN 32 AND 1024
    AND object_key LIKE 'public/event-branding/%'
  ),
  asset_url TEXT NOT NULL CHECK (
    length(asset_url) BETWEEN 32 AND 2000
    AND asset_url LIKE '/api/v1/public/event-assets/%'
  ),
  content_type TEXT NOT NULL CHECK (
    content_type IN ('image/jpeg', 'image/png', 'image/webp')
  ),
  byte_size INTEGER NOT NULL CHECK (byte_size BETWEEN 1 AND 2097152),
  checksum_sha256 BLOB NOT NULL CHECK (length(checksum_sha256) = 32),
  status TEXT NOT NULL CHECK (status IN ('pending', 'attached', 'retired')),
  created_by_user_id TEXT NOT NULL,
  created_at_ms INTEGER NOT NULL,
  attached_at_ms INTEGER,
  FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, event_id)
    REFERENCES events(organization_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  UNIQUE (organization_id, id),
  UNIQUE (object_key),
  UNIQUE (asset_url),
  CHECK (
    (status = 'pending' AND event_id IS NULL AND attached_at_ms IS NULL)
    OR (status = 'attached' AND event_id IS NOT NULL AND attached_at_ms IS NOT NULL)
    OR (status = 'retired' AND event_id IS NOT NULL AND attached_at_ms IS NOT NULL)
  )
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

CREATE TABLE event_label_write_guards (
  id TEXT PRIMARY KEY NOT NULL,
  label_id TEXT NOT NULL,
  applied_changes INTEGER NOT NULL CHECK(applied_changes=1),
  created_at_ms INTEGER NOT NULL
);

CREATE TABLE event_labels (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 80),
  color TEXT NOT NULL CHECK(
    length(color)=7
    AND substr(color,1,1)='#'
    AND substr(color,2) NOT GLOB '*[^0-9A-F]*'
  ),
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','archived')),
  version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  archived_at_ms INTEGER,
  FOREIGN KEY (organization_id,event_id)
    REFERENCES events(organization_id,id) ON DELETE RESTRICT,
  FOREIGN KEY (id) REFERENCES owned_resources(id) ON DELETE RESTRICT,
  UNIQUE (organization_id,event_id,id),
  CHECK(updated_at_ms >= created_at_ms),
  CHECK((status='archived') = (archived_at_ms IS NOT NULL))
);

CREATE TABLE event_memberships (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('event_admin', 'speaker')),
  status TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  revoked_at_ms INTEGER,
  FOREIGN KEY (organization_id, event_id)
    REFERENCES events(organization_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, user_id)
    REFERENCES organization_memberships(organization_id, user_id) ON DELETE RESTRICT,
  UNIQUE (organization_id, event_id, user_id, role)
);

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

CREATE TABLE event_rooms (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  status TEXT NOT NULL CHECK (status IN ('active', 'archived')),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id, event_id)
    REFERENCES events(organization_id, id) ON DELETE RESTRICT,
  UNIQUE (organization_id, event_id, id),
  UNIQUE (organization_id, event_id, name)
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
  withdrawn_at_ms INTEGER, selection_status TEXT NOT NULL DEFAULT 'accepted'
  CHECK (selection_status IN ('submitted', 'accepted', 'rejected')),
  FOREIGN KEY (organization_id, event_id)
    REFERENCES events(organization_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, person_id)
    REFERENCES people(organization_id, id) ON DELETE RESTRICT,
  UNIQUE (organization_id, event_id, id),
  UNIQUE (organization_id, event_id, person_id),
  CHECK ((status = 'withdrawn') = (withdrawn_at_ms IS NOT NULL))
);

CREATE TABLE event_tracks (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  is_exclusive INTEGER NOT NULL DEFAULT 0 CHECK (is_exclusive IN (0, 1)),
  status TEXT NOT NULL CHECK (status IN ('active', 'archived')),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id, event_id)
    REFERENCES events(organization_id, id) ON DELETE RESTRICT,
  UNIQUE (organization_id, event_id, id),
  UNIQUE (organization_id, event_id, name)
);

CREATE TABLE events (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  starts_at_ms INTEGER NOT NULL,
  ends_at_ms INTEGER NOT NULL,
  time_zone TEXT NOT NULL,
  location TEXT,
  delivery_mode TEXT NOT NULL CHECK (delivery_mode IN ('in_person', 'virtual', 'hybrid')),
  description TEXT,
  logo_object_key TEXT,
  accent_color TEXT,
  status TEXT NOT NULL CHECK (status IN ('draft', 'active', 'archived')),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  archived_at_ms INTEGER, logo_url TEXT, website_url TEXT, email_sender_name TEXT
  CHECK (email_sender_name IS NULL OR length(email_sender_name) BETWEEN 1 AND 200), email_reply_to TEXT
  CHECK (email_reply_to IS NULL OR length(email_reply_to) BETWEEN 3 AND 320), cover_image_url TEXT,
  CHECK (ends_at_ms >= starts_at_ms),
  FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT,
  UNIQUE (organization_id, id)
);

CREATE TABLE idempotency_records (
  id TEXT PRIMARY KEY NOT NULL,
  principal_key TEXT NOT NULL,
  organization_id TEXT,
  event_id TEXT,
  route_key TEXT NOT NULL,
  idempotency_key_hash BLOB NOT NULL,
  request_fingerprint BLOB NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('in_progress', 'completed')),
  response_status INTEGER,
  response_resource_type TEXT,
  response_resource_id TEXT,
  created_at_ms INTEGER NOT NULL,
  completed_at_ms INTEGER,
  expires_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, event_id) REFERENCES events(organization_id, id) ON DELETE RESTRICT,
  UNIQUE (principal_key, route_key, idempotency_key_hash),
  CHECK ((state = 'completed') = (completed_at_ms IS NOT NULL))
);

CREATE TABLE "identity_invitations" (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  normalized_email TEXT NOT NULL,
  email TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('organization_admin','event_admin','evaluator','speaker')),
  status TEXT NOT NULL CHECK(status IN ('pending','accepted','revoked','expired')),
  invited_by_user_id TEXT NOT NULL,
  expires_at_ms INTEGER NOT NULL,
  accepted_at_ms INTEGER,
  revoked_at_ms INTEGER,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  display_name TEXT NOT NULL DEFAULT '' CHECK(length(display_name) <= 200),
  job_title TEXT NOT NULL DEFAULT '' CHECK(length(job_title) <= 200),
  company TEXT NOT NULL DEFAULT '' CHECK(length(company) <= 200),
  biography TEXT NOT NULL DEFAULT '' CHECK(length(biography) <= 5000),
  FOREIGN KEY (organization_id,event_id) REFERENCES events(organization_id,id),
  FOREIGN KEY (invited_by_user_id) REFERENCES users(id),
  UNIQUE (organization_id,event_id,normalized_email,role)
);

CREATE TABLE instance_setup (
  singleton_key TEXT PRIMARY KEY NOT NULL CHECK (singleton_key = 'primary'),
  completed_at_ms INTEGER NOT NULL
);

CREATE TABLE instance_setup_credentials (
  singleton_key TEXT PRIMARY KEY NOT NULL CHECK (singleton_key = 'primary'),
  deployment_key TEXT NOT NULL CHECK (
    length(deployment_key) = 64
    AND deployment_key NOT GLOB '*[^0-9a-f]*'
  ),
  generated_at_ms INTEGER NOT NULL
);

CREATE TABLE organization_memberships (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('organization_admin', 'member')),
  status TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  revoked_at_ms INTEGER,
  FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT,
  UNIQUE (organization_id, user_id),
  UNIQUE (organization_id, user_id, id)
);

CREATE TABLE organizations (
  id TEXT PRIMARY KEY NOT NULL,
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  status TEXT NOT NULL CHECK (status IN ('active', 'archived')),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  archived_at_ms INTEGER
);

CREATE TABLE owned_resources (
  id TEXT PRIMARY KEY NOT NULL,
  resource_type TEXT NOT NULL CHECK(resource_type IN (
    'organization','event','program','form','session','track','label','message_template'
  )),
  created_by_user_id TEXT NOT NULL,
  owner_user_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','archived')),
  version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  archived_at_ms INTEGER,
  FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  CHECK(updated_at_ms >= created_at_ms),
  CHECK((status='archived') = (archived_at_ms IS NOT NULL))
);

CREATE TABLE password_authentication_state (
  user_id TEXT PRIMARY KEY NOT NULL,
  consecutive_failures INTEGER NOT NULL DEFAULT 0
    CHECK(consecutive_failures BETWEEN 0 AND 100),
  first_failure_at_ms INTEGER,
  last_failure_at_ms INTEGER,
  blocked_until_ms INTEGER,
  last_success_at_ms INTEGER,
  updated_at_ms INTEGER NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  CHECK(
    (consecutive_failures = 0 AND first_failure_at_ms IS NULL AND last_failure_at_ms IS NULL)
    OR
    (consecutive_failures > 0 AND first_failure_at_ms IS NOT NULL
      AND last_failure_at_ms IS NOT NULL AND last_failure_at_ms >= first_failure_at_ms)
  ),
  CHECK(blocked_until_ms IS NULL OR last_failure_at_ms IS NOT NULL),
  CHECK(last_success_at_ms IS NULL OR last_success_at_ms <= updated_at_ms)
);

CREATE TABLE password_credentials (
  user_id TEXT PRIMARY KEY NOT NULL,
  verifier_phc TEXT NOT NULL
    CHECK(length(verifier_phc) BETWEEN 32 AND 1024)
    CHECK(verifier_phc GLOB '$argon2id$*' OR verifier_phc GLOB '$pbkdf2-sha256$*'),
  pepper_version INTEGER NOT NULL CHECK(pepper_version >= 1),
  status TEXT NOT NULL DEFAULT 'active'
    CHECK(status IN ('active','reset_required','disabled')),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  last_verified_at_ms INTEGER,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  CHECK(updated_at_ms >= created_at_ms),
  CHECK(last_verified_at_ms IS NULL OR last_verified_at_ms >= created_at_ms)
);

CREATE TABLE password_recovery_challenges (
  id TEXT PRIMARY KEY NOT NULL,
  user_id TEXT NOT NULL,
  normalized_email TEXT NOT NULL,
  token_hash BLOB NOT NULL CHECK(length(token_hash) = 32),
  purpose TEXT NOT NULL CHECK(purpose IN ('password_setup','password_reset')),
  requested_ip_hash BLOB CHECK(requested_ip_hash IS NULL OR length(requested_ip_hash) = 32),
  expires_at_ms INTEGER NOT NULL,
  consumed_at_ms INTEGER,
  created_at_ms INTEGER NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  UNIQUE (token_hash),
  CHECK(expires_at_ms > created_at_ms),
  CHECK(consumed_at_ms IS NULL OR consumed_at_ms >= created_at_ms)
);

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

CREATE TABLE resource_access_grants (
  id TEXT PRIMARY KEY NOT NULL,
  resource_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  permission TEXT NOT NULL CHECK(permission IN ('view','edit','manage')),
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','revoked')),
  granted_by_user_id TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  revoked_at_ms INTEGER,
  revoked_by_user_id TEXT,
  FOREIGN KEY (resource_id) REFERENCES owned_resources(id) ON DELETE RESTRICT,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT,
  FOREIGN KEY (granted_by_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  FOREIGN KEY (revoked_by_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  UNIQUE(resource_id,user_id,permission),
  CHECK(updated_at_ms >= created_at_ms),
  CHECK(
    (status='active' AND revoked_at_ms IS NULL AND revoked_by_user_id IS NULL)
    OR
    (status='revoked' AND revoked_at_ms IS NOT NULL AND revoked_by_user_id IS NOT NULL)
  )
);

CREATE TABLE resource_ownership_transfers (
  id TEXT PRIMARY KEY NOT NULL,
  resource_id TEXT NOT NULL,
  from_user_id TEXT NOT NULL,
  to_user_id TEXT NOT NULL,
  transferred_by_user_id TEXT NOT NULL,
  reason TEXT CHECK(reason IS NULL OR length(reason) <= 1000),
  transferred_at_ms INTEGER NOT NULL,
  FOREIGN KEY (resource_id) REFERENCES owned_resources(id) ON DELETE RESTRICT,
  FOREIGN KEY (from_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  FOREIGN KEY (to_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  FOREIGN KEY (transferred_by_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  CHECK(from_user_id != to_user_id)
);

CREATE TABLE schedule_revisions (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  revision_number INTEGER NOT NULL CHECK (revision_number >= 1),
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  status TEXT NOT NULL CHECK (status IN ('draft', 'published', 'superseded')),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_by_user_id TEXT NOT NULL,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  published_at_ms INTEGER,
  FOREIGN KEY (organization_id, event_id)
    REFERENCES events(organization_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  UNIQUE (organization_id, event_id, id),
  UNIQUE (organization_id, event_id, revision_number),
  CHECK (
    (status='draft' AND published_at_ms IS NULL)
    OR (status IN ('published','superseded') AND published_at_ms IS NOT NULL)
  )
);

CREATE TABLE session_active_roles (
  session_id TEXT PRIMARY KEY NOT NULL,
  user_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('organizer','reviewer','speaker')),
  selected_at_ms INTEGER NOT NULL,
  FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
  FOREIGN KEY (user_id,role) REFERENCES user_roles(user_id,role) ON DELETE CASCADE
);

CREATE TABLE session_content_versions (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  accepted_session_id TEXT NOT NULL,
  version INTEGER NOT NULL CHECK (version >= 1),
  title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
  abstract TEXT NOT NULL CHECK (length(abstract) BETWEEN 1 AND 5000),
  content_status TEXT NOT NULL CHECK (content_status IN ('draft', 'approved')),
  changed_by_user_id TEXT NOT NULL,
  created_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id,event_id,accepted_session_id)
    REFERENCES accepted_sessions(organization_id,event_id,id) ON DELETE RESTRICT,
  FOREIGN KEY (changed_by_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  UNIQUE (organization_id,event_id,id),
  UNIQUE (accepted_session_id,version)
);

CREATE TABLE session_content_write_guards (
  id TEXT PRIMARY KEY NOT NULL,
  accepted_session_id TEXT NOT NULL,
  applied_changes INTEGER NOT NULL CHECK (applied_changes = 1),
  created_at_ms INTEGER NOT NULL
);

CREATE TABLE session_label_write_guards (
  id TEXT PRIMARY KEY NOT NULL,
  accepted_session_id TEXT NOT NULL,
  applied_changes INTEGER NOT NULL CHECK(applied_changes=1),
  created_at_ms INTEGER NOT NULL
);

CREATE TABLE sessions (
  id TEXT PRIMARY KEY NOT NULL,
  user_id TEXT NOT NULL,
  token_hash BLOB NOT NULL,
  csrf_secret_hash BLOB NOT NULL,
  authorization_version INTEGER NOT NULL CHECK (authorization_version >= 1),
  created_at_ms INTEGER NOT NULL,
  last_seen_at_ms INTEGER NOT NULL,
  idle_expires_at_ms INTEGER NOT NULL,
  absolute_expires_at_ms INTEGER NOT NULL,
  revoked_at_ms INTEGER,
  revoke_reason TEXT,
  rotated_from_session_id TEXT,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (rotated_from_session_id) REFERENCES sessions(id) ON DELETE SET NULL,
  UNIQUE (token_hash),
  CHECK (idle_expires_at_ms <= absolute_expires_at_ms)
);

CREATE TABLE speaker_asset_versions (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  event_speaker_id TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  generation INTEGER NOT NULL CHECK (generation >= 1),
  object_key TEXT NOT NULL CHECK (length(object_key) BETWEEN 16 AND 1024),
  original_filename TEXT NOT NULL CHECK (length(original_filename) BETWEEN 1 AND 255),
  content_type TEXT CHECK (content_type IS NULL OR length(content_type) BETWEEN 1 AND 255),
  byte_size INTEGER CHECK (byte_size IS NULL OR byte_size >= 0),
  checksum_sha256 BLOB CHECK (checksum_sha256 IS NULL OR length(checksum_sha256) = 32),
  scan_state TEXT NOT NULL
    CHECK (scan_state IN (
      'pending_upload', 'uploaded', 'scanning', 'clean', 'rejected', 'superseded'
    )),
  is_current INTEGER NOT NULL DEFAULT 0 CHECK (is_current IN (0, 1)),
  created_at_ms INTEGER NOT NULL,
  uploaded_at_ms INTEGER,
  scan_started_at_ms INTEGER,
  scanned_at_ms INTEGER,
  scan_result_code TEXT CHECK (scan_result_code IS NULL OR length(scan_result_code) <= 100), version_comment TEXT NOT NULL DEFAULT 'Legacy upload'
CHECK(length(trim(version_comment)) BETWEEN 1 AND 1000),
  FOREIGN KEY (organization_id, event_id, event_speaker_id, asset_id)
    REFERENCES speaker_assets(organization_id, event_id, event_speaker_id, id)
      ON DELETE RESTRICT,
  UNIQUE (organization_id, event_id, id),
  UNIQUE (organization_id, event_id, event_speaker_id, id),
  UNIQUE (asset_id, generation),
  UNIQUE (object_key),
  CHECK (is_current = 0 OR scan_state = 'clean'),
  CHECK (
    (scan_state = 'pending_upload'
      AND uploaded_at_ms IS NULL AND scan_started_at_ms IS NULL AND scanned_at_ms IS NULL
      AND content_type IS NULL AND byte_size IS NULL AND checksum_sha256 IS NULL)
    OR
    (scan_state = 'uploaded'
      AND uploaded_at_ms IS NOT NULL AND scan_started_at_ms IS NULL AND scanned_at_ms IS NULL
      AND content_type IS NOT NULL AND byte_size IS NOT NULL AND checksum_sha256 IS NOT NULL)
    OR
    (scan_state = 'scanning'
      AND uploaded_at_ms IS NOT NULL AND scan_started_at_ms IS NOT NULL AND scanned_at_ms IS NULL
      AND content_type IS NOT NULL AND byte_size IS NOT NULL AND checksum_sha256 IS NOT NULL)
    OR
    (scan_state IN ('clean', 'rejected', 'superseded')
      AND uploaded_at_ms IS NOT NULL AND scan_started_at_ms IS NOT NULL
      AND scanned_at_ms IS NOT NULL AND content_type IS NOT NULL
      AND byte_size IS NOT NULL AND checksum_sha256 IS NOT NULL)
  )
);

CREATE TABLE speaker_assets (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  event_speaker_id TEXT NOT NULL,
  submission_id TEXT,
  task_id TEXT,
  kind TEXT NOT NULL CHECK (kind IN ('headshot', 'slides', 'supporting_document')),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id, event_id, event_speaker_id)
    REFERENCES event_speakers(organization_id, event_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, event_id, submission_id)
    REFERENCES submissions(organization_id, event_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, event_id, event_speaker_id, task_id)
    REFERENCES speaker_tasks(organization_id, event_id, event_speaker_id, id)
      ON DELETE RESTRICT,
  UNIQUE (organization_id, event_id, id),
  UNIQUE (organization_id, event_id, event_speaker_id, id)
);

CREATE TABLE speaker_tasks (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  event_speaker_id TEXT,
  pending_invitation_id TEXT,
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
  updated_at_ms INTEGER NOT NULL, form_schema_json TEXT
  CHECK (form_schema_json IS NULL OR json_valid(form_schema_json)), response_json TEXT
  CHECK (response_json IS NULL OR json_valid(response_json)), responded_at_ms INTEGER,
  FOREIGN KEY (organization_id, event_id, event_speaker_id)
    REFERENCES event_speakers(organization_id, event_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (pending_invitation_id)
    REFERENCES identity_invitations(id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, event_id, submission_id)
    REFERENCES submissions(organization_id, event_id, id) ON DELETE RESTRICT,
  UNIQUE (organization_id, event_id, id),
  CHECK ((event_speaker_id IS NOT NULL) != (pending_invitation_id IS NOT NULL)),
  CHECK ((state = 'completed') = (completed_at_ms IS NOT NULL)),
  CHECK ((state = 'waived') = (waived_at_ms IS NOT NULL)),
  CHECK (completed_at_ms IS NULL OR waived_at_ms IS NULL)
);

CREATE TABLE submission_contributor_invitation_guards (
  id TEXT PRIMARY KEY NOT NULL,
  contributor_id TEXT NOT NULL,
  applied_changes INTEGER NOT NULL CHECK(applied_changes=1),
  created_at_ms INTEGER NOT NULL,
  FOREIGN KEY(contributor_id) REFERENCES submission_contributors(id) ON DELETE CASCADE
);

CREATE TABLE "submission_contributors" (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  submission_id TEXT NOT NULL,
  display_name TEXT NOT NULL CHECK(length(display_name) BETWEEN 1 AND 200),
  email TEXT NOT NULL CHECK(length(email) BETWEEN 3 AND 320),
  normalized_email TEXT NOT NULL CHECK(length(normalized_email) BETWEEN 3 AND 320),
  role TEXT NOT NULL DEFAULT 'co_speaker'
    CHECK(role IN ('co_speaker','co_author','moderator','panelist','other')),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL, invitation_status TEXT NOT NULL DEFAULT 'removed'
  CHECK(invitation_status IN ('pending','accepted','declined','removed')), invitation_token_hash BLOB, invitation_expires_at_ms INTEGER, invited_at_ms INTEGER, accepted_at_ms INTEGER, declined_at_ms INTEGER, removed_at_ms INTEGER, user_id TEXT REFERENCES users(id), invitation_version INTEGER NOT NULL DEFAULT 0
  CHECK(invitation_version >= 0),
  FOREIGN KEY (organization_id,event_id,submission_id)
    REFERENCES "submissions"(organization_id,event_id,id) ON DELETE CASCADE,
  UNIQUE (submission_id,normalized_email)
);

CREATE TABLE submission_decisions (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  round_id TEXT,
  submission_id TEXT NOT NULL,
  decision TEXT NOT NULL CHECK (decision IN ('accepted', 'rejected')),
  internal_reason TEXT NOT NULL CHECK (length(internal_reason) <= 2000),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  decided_by_user_id TEXT NOT NULL,
  decided_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  FOREIGN KEY (round_id) REFERENCES evaluation_rounds(id) ON DELETE RESTRICT,
  FOREIGN KEY (submission_id) REFERENCES submissions(id) ON DELETE RESTRICT,
  FOREIGN KEY (decided_by_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  UNIQUE (round_id, submission_id),
  UNIQUE (submission_id)
);

CREATE TABLE "submission_drafts" (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  form_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  answers_json TEXT NOT NULL CHECK(json_valid(answers_json)),
  version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id,event_id)
    REFERENCES events(organization_id,id) ON DELETE RESTRICT,
  FOREIGN KEY (form_id) REFERENCES call_for_speaker_forms(id),
  FOREIGN KEY (user_id) REFERENCES users(id),
  UNIQUE (form_id,user_id)
);

CREATE TABLE submission_speakers (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  submission_id TEXT NOT NULL,
  event_speaker_id TEXT NOT NULL,
  role TEXT NOT NULL
    CHECK (role IN ('primary','co_speaker','co_author','moderator','panelist','other')),
  snapshot_name TEXT NOT NULL CHECK (length(snapshot_name) BETWEEN 1 AND 200),
  created_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id, event_id, submission_id)
    REFERENCES submissions(organization_id, event_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, event_id, event_speaker_id)
    REFERENCES event_speakers(organization_id, event_id, id) ON DELETE RESTRICT,
  UNIQUE (organization_id, event_id, submission_id, event_speaker_id)
);

CREATE TABLE submission_write_guards (
  id TEXT PRIMARY KEY NOT NULL,
  submission_id TEXT NOT NULL,
  applied_changes INTEGER NOT NULL CHECK (applied_changes = 1),
  created_at_ms INTEGER NOT NULL
);

CREATE TABLE "submissions" (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  form_id TEXT NOT NULL,
  public_session_id TEXT NOT NULL,
  proposal_title TEXT NOT NULL CHECK (length(proposal_title) BETWEEN 1 AND 200),
  proposal_abstract TEXT NOT NULL CHECK (length(proposal_abstract) BETWEEN 1 AND 5000),
  speaker_name TEXT NOT NULL CHECK (length(speaker_name) BETWEEN 1 AND 200),
  status TEXT NOT NULL CHECK (status IN ('submitted', 'withdrawn')),
  submitted_at_ms INTEGER NOT NULL,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  speaker_email TEXT NOT NULL DEFAULT '',
  submitter_user_id TEXT REFERENCES users(id),
  answers_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(answers_json)),
  routed_category TEXT,
  routed_track TEXT,
  routed_review_queue TEXT,
  version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
  FOREIGN KEY (organization_id,event_id)
    REFERENCES events(organization_id,id) ON DELETE RESTRICT,
  FOREIGN KEY (form_id) REFERENCES call_for_speaker_forms(id) ON DELETE RESTRICT,
  UNIQUE (public_session_id,form_id),
  UNIQUE (organization_id,event_id,id)
);

CREATE TABLE upload_intents (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  event_speaker_id TEXT NOT NULL,
  asset_version_id TEXT NOT NULL,
  purpose TEXT NOT NULL CHECK (purpose IN ('create', 'replace')),
  token_hash BLOB NOT NULL CHECK (length(token_hash) = 32),
  expected_content_type TEXT NOT NULL
    CHECK (length(expected_content_type) BETWEEN 1 AND 255),
  expected_byte_size INTEGER NOT NULL CHECK (expected_byte_size >= 0),
  expected_checksum_sha256 BLOB NOT NULL CHECK (length(expected_checksum_sha256) = 32),
  expires_at_ms INTEGER NOT NULL,
  consumed_at_ms INTEGER,
  created_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id, event_id, event_speaker_id)
    REFERENCES event_speakers(organization_id, event_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, event_id, event_speaker_id, asset_version_id)
    REFERENCES speaker_asset_versions(organization_id, event_id, event_speaker_id, id)
      ON DELETE RESTRICT,
  UNIQUE (token_hash),
  CHECK (expires_at_ms > created_at_ms),
  CHECK (consumed_at_ms IS NULL OR consumed_at_ms >= created_at_ms)
);

CREATE TABLE user_headshots (
  user_id TEXT PRIMARY KEY NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  object_key TEXT NOT NULL UNIQUE,
  content_type TEXT NOT NULL CHECK(content_type IN ('image/jpeg','image/png','image/webp')),
  byte_size INTEGER NOT NULL CHECK(byte_size BETWEEN 1 AND 5242880),
  checksum_sha256 BLOB NOT NULL CHECK(length(checksum_sha256)=32),
  updated_at_ms INTEGER NOT NULL
);

CREATE TABLE user_roles (
  user_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('organizer','reviewer','speaker')),
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','revoked')),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  revoked_at_ms INTEGER, is_default INTEGER NOT NULL DEFAULT 0
  CHECK(is_default IN (0,1)),
  PRIMARY KEY (user_id,role),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  CHECK(updated_at_ms >= created_at_ms),
  CHECK((status='revoked') = (revoked_at_ms IS NOT NULL))
);

CREATE TABLE users (
  id TEXT PRIMARY KEY NOT NULL,
  email TEXT NOT NULL,
  normalized_email TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active', 'suspended', 'deleted')),
  email_verified_at_ms INTEGER,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  authorization_version INTEGER NOT NULL DEFAULT 1 CHECK (authorization_version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  deleted_at_ms INTEGER
, display_name TEXT
CHECK (display_name IS NULL OR length(display_name) BETWEEN 1 AND 200), job_title TEXT
CHECK (job_title IS NULL OR length(job_title) <= 200), company TEXT
CHECK (company IS NULL OR length(company) <= 200), time_zone TEXT
CHECK (time_zone IS NULL OR length(time_zone) <= 100), profile_completed_at_ms INTEGER
CHECK (profile_completed_at_ms IS NULL OR profile_completed_at_ms > 0), first_name TEXT
  CHECK(first_name IS NULL OR length(first_name) BETWEEN 1 AND 100), last_name TEXT
  CHECK(last_name IS NULL OR length(last_name) BETWEEN 1 AND 100), description TEXT
  CHECK(description IS NULL OR length(description) <= 1000), website_url TEXT
  CHECK(website_url IS NULL OR length(website_url) <= 500), linkedin_url TEXT
  CHECK(linkedin_url IS NULL OR length(linkedin_url) <= 500), x_url TEXT
  CHECK(x_url IS NULL OR length(x_url) <= 500), public_profile_enabled INTEGER NOT NULL DEFAULT 0
  CHECK(public_profile_enabled IN (0,1)));

CREATE INDEX idx_accepted_session_labels_event
  ON accepted_session_labels(organization_id,event_id,label_id,accepted_session_id);

CREATE INDEX idx_accepted_sessions_event
  ON accepted_sessions(organization_id,event_id,created_at_ms DESC,id DESC);

CREATE INDEX idx_accepted_sessions_public_content
  ON accepted_sessions(organization_id,event_id,content_status,id);

CREATE INDEX idx_agenda_items_revision_time
  ON agenda_items(organization_id,event_id,revision_id,starts_at_ms,id);

CREATE INDEX idx_agenda_items_room_time
  ON agenda_items(revision_id,room_id,starts_at_ms,ends_at_ms,id);

CREATE INDEX idx_agenda_items_track_time
  ON agenda_items(revision_id,track_id,starts_at_ms,ends_at_ms,id);

CREATE INDEX idx_agenda_speakers_conflict
  ON agenda_item_speakers(revision_id,event_speaker_id,agenda_item_id);

CREATE INDEX idx_ai_triage_submission_recent
  ON ai_triage_results(organization_id,event_id,submission_id,generated_at_ms DESC,id DESC);

CREATE INDEX idx_asset_download_grants_expiry
  ON asset_download_grants(expires_at_ms, id)
  WHERE consumed_at_ms IS NULL;

CREATE INDEX idx_asset_download_grants_principal_recent
  ON asset_download_grants(
    organization_id, event_id, principal_user_id, created_at_ms DESC, id DESC
  );

CREATE INDEX idx_asset_scan_events_job
  ON asset_scan_events(engine, job_id, received_at_ms DESC, id DESC);

CREATE INDEX idx_asset_scan_events_verdict_recent
  ON asset_scan_events(verdict, received_at_ms DESC, id DESC);

CREATE INDEX idx_asset_scan_events_version_recent
  ON asset_scan_events(
    organization_id, event_id, asset_version_id, received_at_ms DESC, id DESC
  );

CREATE INDEX idx_attempts_message
  ON communication_delivery_attempts(organization_id,event_id,message_id,attempt_number);

CREATE INDEX idx_audit_event_time
  ON audit_events(organization_id, event_id, occurred_at_ms DESC, id DESC);

CREATE INDEX idx_audit_target_time
  ON audit_events(organization_id, target_type, target_id, occurred_at_ms DESC, id DESC);

CREATE INDEX idx_auth_challenges_expiry ON authentication_challenges(expires_at_ms);

CREATE INDEX idx_auth_challenges_token_live
  ON authentication_challenges(token_hash,expires_at_ms) WHERE consumed_at_ms IS NULL;

CREATE INDEX idx_calendar_invitations_agenda
  ON calendar_invitations(organization_id,event_id,agenda_item_id,recipient_user_id);

CREATE INDEX idx_calendar_versions_delivery
  ON calendar_invitation_versions(organization_id,event_id,communication_message_id);

CREATE INDEX idx_cfp_form_write_guards_form
  ON cfp_form_write_guards(form_id,created_at_ms DESC);

CREATE INDEX idx_cfp_forms_event_published
  ON call_for_speaker_forms(
    organization_id,event_id,version DESC,published_at_ms DESC,id DESC
  ) WHERE status = 'published';

CREATE INDEX idx_cfp_forms_event_version
  ON call_for_speaker_forms(organization_id,event_id,version DESC);

CREATE INDEX idx_cfp_staged_assets_expiry
  ON cfp_staged_assets(expires_at_ms, id);

CREATE INDEX idx_cfp_staged_assets_owner
  ON cfp_staged_assets(form_id, user_id, status, created_at_ms, id);

CREATE INDEX idx_communication_templates_event_kind
  ON communication_templates(organization_id,event_id,kind,updated_at_ms DESC,id DESC);

CREATE INDEX idx_evaluation_assignments_evaluator
  ON evaluation_assignments(evaluator_user_id, status, created_at_ms DESC, id DESC);

CREATE INDEX idx_evaluation_assignments_round_status
  ON evaluation_assignments(round_id,status,submission_id,evaluator_user_id,id);

CREATE INDEX idx_evaluation_conflicts_round
  ON evaluation_conflicts(organization_id, event_id, round_id, declared_at_ms DESC);

CREATE INDEX idx_evaluation_rounds_event
  ON evaluation_rounds(organization_id,event_id,status,created_at_ms DESC);

CREATE INDEX idx_evaluation_rounds_review_window
  ON evaluation_rounds(status,review_opens_at_ms,review_closes_at_ms,event_id);

CREATE INDEX idx_evaluations_round
  ON evaluations(round_id, state, updated_at_ms DESC);

CREATE INDEX idx_event_branding_assets_pending
  ON event_branding_assets(organization_id, status, created_at_ms, id);

CREATE UNIQUE INDEX idx_event_labels_active_name
  ON event_labels(organization_id,event_id,lower(name)) WHERE status='active';

CREATE INDEX idx_event_labels_event_status
  ON event_labels(organization_id,event_id,status,lower(name),id);

CREATE INDEX idx_event_memberships_event_status
  ON event_memberships(organization_id,event_id,status,role,user_id);

CREATE INDEX idx_event_memberships_user_active
  ON event_memberships(user_id, organization_id, event_id, role) WHERE status = 'active';

CREATE INDEX idx_event_resources_portal
  ON event_resources(organization_id,event_id,status,sort_order,title,id);

CREATE INDEX idx_event_speakers_event_status_activity
  ON event_speakers(organization_id, event_id, status, last_activity_at_ms DESC, id DESC);

CREATE INDEX idx_event_speakers_selection
  ON event_speakers(organization_id,event_id,selection_status,last_activity_at_ms DESC,id DESC);

CREATE INDEX idx_events_org_starts
  ON events(organization_id,starts_at_ms DESC,id DESC);

CREATE INDEX idx_events_org_status_updated
  ON events(organization_id, status, updated_at_ms DESC, id DESC);

CREATE INDEX idx_forms_public_availability
  ON call_for_speaker_forms(slug,status,opens_at_ms,closes_at_ms);

CREATE INDEX idx_idempotency_expiry ON idempotency_records(expires_at_ms);

CREATE INDEX idx_identity_invitations_email
  ON identity_invitations(normalized_email,status,expires_at_ms,id);

CREATE INDEX idx_identity_invitations_event_recent
  ON identity_invitations(organization_id,event_id,created_at_ms DESC,id DESC);

CREATE INDEX idx_integration_tokens_lookup
  ON event_integration_tokens(token_hash,status,event_id);

CREATE INDEX idx_messages_admin
  ON communication_messages(organization_id,event_id,updated_at_ms DESC,id DESC);

CREATE INDEX idx_messages_delivery ON communication_messages(status,queued_at_ms,id);

CREATE INDEX idx_messages_dispatch
ON communication_messages(status,updated_at_ms,id);

CREATE INDEX idx_org_memberships_user_active
  ON organization_memberships(user_id, organization_id) WHERE status = 'active';

CREATE INDEX idx_owned_resources_owner
  ON owned_resources(owner_user_id,resource_type,status,updated_at_ms DESC);

CREATE INDEX idx_password_recovery_user_active
  ON password_recovery_challenges(user_id,purpose,consumed_at_ms,expires_at_ms DESC,id);

CREATE INDEX idx_people_org_user
  ON people(organization_id, user_id);

CREATE INDEX idx_reminder_schedules_task
  ON reminder_schedules(organization_id,event_id,task_id,state,send_at_ms,id);

CREATE INDEX idx_reminders_due ON reminder_schedules(state,send_at_ms,id);

CREATE INDEX idx_resource_access_grants_resource_active
  ON resource_access_grants(resource_id,permission,user_id) WHERE status='active';

CREATE INDEX idx_resource_access_grants_user_active
  ON resource_access_grants(user_id,resource_id,permission) WHERE status='active';

CREATE INDEX idx_resource_ownership_transfers_resource
  ON resource_ownership_transfers(resource_id,transferred_at_ms DESC);

CREATE INDEX idx_session_active_roles_user
  ON session_active_roles(user_id,session_id);

CREATE INDEX idx_session_content_history
  ON session_content_versions(accepted_session_id,version DESC);

CREATE INDEX idx_sessions_idle_expiry
  ON sessions(idle_expires_at_ms) WHERE revoked_at_ms IS NULL;

CREATE INDEX idx_sessions_user_active
  ON sessions(user_id, absolute_expires_at_ms) WHERE revoked_at_ms IS NULL;

CREATE INDEX idx_speaker_asset_versions_current
  ON speaker_asset_versions(organization_id, event_id, asset_id, is_current, generation DESC);

CREATE INDEX idx_speaker_asset_versions_history
  ON speaker_asset_versions(organization_id,event_id,asset_id,generation DESC,id);

CREATE INDEX idx_speaker_asset_versions_scanner
  ON speaker_asset_versions(scan_state, uploaded_at_ms, id);

CREATE INDEX idx_speaker_assets_owner
  ON speaker_assets(
    organization_id, event_id, event_speaker_id, kind, updated_at_ms DESC, id DESC
  );

CREATE INDEX idx_speaker_assets_task
  ON speaker_assets(organization_id, event_id, task_id, kind, id);

CREATE INDEX idx_speaker_tasks_dashboard
  ON speaker_tasks(organization_id, event_id, state, due_at_ms, id);

CREATE INDEX idx_speaker_tasks_dashboard_all_deadline
  ON speaker_tasks(
    organization_id,
    event_id,
    (due_at_ms IS NULL),
    due_at_ms,
    id
  );

CREATE INDEX idx_speaker_tasks_dashboard_state_deadline
  ON speaker_tasks(
    organization_id,
    event_id,
    state,
    (due_at_ms IS NULL),
    due_at_ms,
    id
  );

CREATE INDEX idx_speaker_tasks_dashboard_type_deadline
  ON speaker_tasks(
    organization_id,
    event_id,
    task_type,
    (due_at_ms IS NULL),
    due_at_ms,
    id
  );

CREATE INDEX idx_speaker_tasks_dashboard_type_state_deadline
  ON speaker_tasks(
    organization_id,
    event_id,
    task_type,
    state,
    (due_at_ms IS NULL),
    due_at_ms,
    id
  );

CREATE INDEX idx_speaker_tasks_portal
  ON speaker_tasks(organization_id, event_id, event_speaker_id, state, due_at_ms, id);

CREATE INDEX idx_speaker_tasks_portal_state_deadline
  ON speaker_tasks(
    organization_id,
    event_id,
    event_speaker_id,
    state,
    (due_at_ms IS NULL),
    due_at_ms,
    id
  );

CREATE INDEX idx_speaker_tasks_submission
  ON speaker_tasks(organization_id, event_id, submission_id, state, id);

CREATE UNIQUE INDEX idx_submission_contributors_invitation_token
  ON submission_contributors(invitation_token_hash)
  WHERE invitation_token_hash IS NOT NULL;

CREATE INDEX idx_submission_contributors_submission
  ON submission_contributors(organization_id,event_id,submission_id,display_name,id);

CREATE INDEX idx_submission_contributors_user
  ON submission_contributors(organization_id,event_id,user_id,invitation_status)
  WHERE user_id IS NOT NULL;

CREATE INDEX idx_submission_decisions_round
  ON submission_decisions(organization_id, event_id, round_id, decided_at_ms DESC);

CREATE INDEX idx_submission_decisions_submission
  ON submission_decisions(organization_id,event_id,submission_id,decided_at_ms DESC,id DESC);

CREATE INDEX idx_submission_drafts_event
  ON submission_drafts(organization_id,event_id,updated_at_ms DESC,id DESC);

CREATE INDEX idx_submission_drafts_user
  ON submission_drafts(user_id,updated_at_ms DESC,id DESC);

CREATE INDEX idx_submission_speakers_speaker
  ON submission_speakers(organization_id, event_id, event_speaker_id, created_at_ms DESC, id DESC);

CREATE INDEX idx_submissions_event_recent
  ON submissions(organization_id,event_id,submitted_at_ms DESC,id DESC);

CREATE INDEX idx_submissions_form_count
  ON submissions(form_id,status,submitted_at_ms,id);

CREATE INDEX idx_submissions_form_owner_recent
  ON submissions(form_id,submitter_user_id,status,updated_at_ms DESC,id DESC);

CREATE INDEX idx_submissions_submitter
  ON submissions(submitter_user_id,submitted_at_ms DESC,id DESC);

CREATE INDEX idx_upload_intents_expiry
  ON upload_intents(expires_at_ms, id)
  WHERE consumed_at_ms IS NULL;

CREATE INDEX idx_upload_intents_owner
  ON upload_intents(
    organization_id, event_id, event_speaker_id, created_at_ms DESC, id DESC
  );

CREATE INDEX idx_user_roles_active
  ON user_roles(user_id,status,role);

CREATE UNIQUE INDEX idx_user_roles_one_default
  ON user_roles(user_id) WHERE is_default=1;

CREATE UNIQUE INDEX uq_calendar_invitation_tenant_id
  ON calendar_invitations(organization_id,event_id,id);

CREATE UNIQUE INDEX uq_evaluation_rounds_event_open
  ON evaluation_rounds(organization_id,event_id) WHERE status='open';

CREATE UNIQUE INDEX uq_event_branding_assets_current
  ON event_branding_assets(organization_id, event_id, kind)
  WHERE status = 'attached';

CREATE UNIQUE INDEX uq_messages_organization_key_without_event
  ON communication_messages(organization_id,deterministic_key) WHERE event_id IS NULL;

CREATE UNIQUE INDEX uq_schedule_revision_draft
  ON schedule_revisions(organization_id, event_id) WHERE status='draft';

CREATE UNIQUE INDEX uq_schedule_revision_published
  ON schedule_revisions(organization_id, event_id) WHERE status='published';

CREATE UNIQUE INDEX uq_speaker_asset_current_clean
  ON speaker_asset_versions(asset_id)
  WHERE is_current = 1;

CREATE UNIQUE INDEX uq_speaker_asset_logical_slot
  ON speaker_assets(
    organization_id,
    event_id,
    event_speaker_id,
    COALESCE(submission_id, ''),
    COALESCE(task_id, ''),
    kind
  );

CREATE UNIQUE INDEX uq_speaker_asset_version_scan_identity
  ON speaker_asset_versions(
    organization_id,
    event_id,
    id,
    generation,
    checksum_sha256
  );

CREATE UNIQUE INDEX uq_speaker_tasks_owner_id
  ON speaker_tasks(organization_id, event_id, event_speaker_id, id);

CREATE UNIQUE INDEX uq_submission_decisions_final
  ON submission_decisions(organization_id,event_id,submission_id);

CREATE UNIQUE INDEX uq_submission_speakers_primary
  ON submission_speakers(organization_id, event_id, submission_id)
  WHERE role = 'primary';

CREATE UNIQUE INDEX uq_users_normalized_email ON users(normalized_email);

CREATE TRIGGER attach_event_branding_insert
AFTER INSERT ON events
BEGIN
  UPDATE event_branding_assets
  SET event_id = NEW.id, status = 'attached', attached_at_ms = NEW.created_at_ms
  WHERE organization_id = NEW.organization_id AND kind = 'logo'
    AND asset_url = NEW.logo_url AND status = 'pending' AND event_id IS NULL;
  UPDATE event_branding_assets
  SET event_id = NEW.id, status = 'attached', attached_at_ms = NEW.created_at_ms
  WHERE organization_id = NEW.organization_id AND kind = 'cover'
    AND asset_url = NEW.cover_image_url AND status = 'pending' AND event_id IS NULL;
END;

CREATE TRIGGER attach_event_branding_update
AFTER UPDATE OF logo_url, cover_image_url ON events
BEGIN
  UPDATE event_branding_assets
  SET status = 'retired'
  WHERE organization_id = OLD.organization_id AND event_id = OLD.id
    AND kind = 'logo' AND status = 'attached'
    AND COALESCE(OLD.logo_url, '') != COALESCE(NEW.logo_url, '');
  UPDATE event_branding_assets
  SET status = 'retired'
  WHERE organization_id = OLD.organization_id AND event_id = OLD.id
    AND kind = 'cover' AND status = 'attached'
    AND COALESCE(OLD.cover_image_url, '') != COALESCE(NEW.cover_image_url, '');
  UPDATE event_branding_assets
  SET event_id = NEW.id, status = 'attached', attached_at_ms = NEW.updated_at_ms
  WHERE organization_id = NEW.organization_id AND kind = 'logo'
    AND asset_url = NEW.logo_url AND status = 'pending' AND event_id IS NULL;
  UPDATE event_branding_assets
  SET event_id = NEW.id, status = 'attached', attached_at_ms = NEW.updated_at_ms
  WHERE organization_id = NEW.organization_id AND kind = 'cover'
    AND asset_url = NEW.cover_image_url AND status = 'pending' AND event_id IS NULL;
END;

CREATE TRIGGER consume_setup_credentials_after_completion
AFTER INSERT ON instance_setup
BEGIN
  DELETE FROM instance_setup_credentials WHERE singleton_key = 'primary';
END;

CREATE TRIGGER consume_setup_credentials_after_organization_creation
AFTER INSERT ON organizations
BEGIN
  DELETE FROM instance_setup_credentials WHERE singleton_key = 'primary';
END;

CREATE TRIGGER instance_setup_completion_cannot_be_changed
BEFORE UPDATE ON instance_setup
BEGIN
  SELECT RAISE(ABORT, 'instance setup completion is permanent');
END;

CREATE TRIGGER instance_setup_completion_cannot_be_deleted
BEFORE DELETE ON instance_setup
BEGIN
  SELECT RAISE(ABORT, 'instance setup completion is permanent');
END;

CREATE TRIGGER owned_resources_creator_is_immutable
BEFORE UPDATE OF created_by_user_id ON owned_resources
WHEN NEW.created_by_user_id != OLD.created_by_user_id
BEGIN
  SELECT RAISE(ABORT, 'resource creator is immutable');
END;

CREATE TRIGGER password_rotation_requires_authorization_bump
BEFORE UPDATE OF verifier_phc,pepper_version ON password_credentials
WHEN NOT EXISTS (
  SELECT 1 FROM users u
  WHERE u.id=NEW.user_id AND u.authorization_version > (
    SELECT authorization_version FROM sessions s
    WHERE s.user_id=NEW.user_id AND s.revoked_at_ms IS NULL
    ORDER BY s.authorization_version DESC LIMIT 1
  )
)
AND EXISTS (
  SELECT 1 FROM sessions s WHERE s.user_id=NEW.user_id AND s.revoked_at_ms IS NULL
)
BEGIN
  SELECT RAISE(ABORT, 'password rotation requires authorization bump');
END;

CREATE TRIGGER prevent_asset_version_comment_update
BEFORE UPDATE OF version_comment ON speaker_asset_versions
WHEN NEW.version_comment != OLD.version_comment
BEGIN
  SELECT RAISE(ABORT, 'asset version comment is immutable');
END;

CREATE TRIGGER prevent_old_asset_version_current_insert
BEFORE INSERT ON speaker_asset_versions
WHEN NEW.is_current = 1 AND EXISTS (
  SELECT 1 FROM speaker_asset_versions newer
  WHERE newer.asset_id = NEW.asset_id AND newer.generation > NEW.generation
)
BEGIN
  SELECT RAISE(ABORT, 'newer asset generation exists');
END;

CREATE TRIGGER prevent_old_asset_version_current_update
BEFORE UPDATE OF is_current, scan_state ON speaker_asset_versions
WHEN NEW.is_current = 1 AND EXISTS (
  SELECT 1 FROM speaker_asset_versions newer
  WHERE newer.asset_id = NEW.asset_id AND newer.generation > NEW.generation
)
BEGIN
  SELECT RAISE(ABORT, 'newer asset generation exists');
END;

CREATE TRIGGER prevent_published_agenda_item_delete
BEFORE DELETE ON agenda_items
WHEN EXISTS (SELECT 1 FROM schedule_revisions r WHERE r.id=OLD.revision_id AND r.status='published')
BEGIN
  SELECT RAISE(ABORT, 'published agenda revision is immutable');
END;

CREATE TRIGGER prevent_published_agenda_speaker_delete
BEFORE DELETE ON agenda_item_speakers
WHEN EXISTS (SELECT 1 FROM schedule_revisions r WHERE r.id=OLD.revision_id AND r.status='published')
BEGIN
  SELECT RAISE(ABORT, 'published agenda revision is immutable');
END;

CREATE TRIGGER prevent_published_agenda_speaker_insert
BEFORE INSERT ON agenda_item_speakers
WHEN EXISTS (SELECT 1 FROM schedule_revisions r WHERE r.id=NEW.revision_id AND r.status='published')
BEGIN
  SELECT RAISE(ABORT, 'published agenda revision is immutable');
END;

CREATE TRIGGER prevent_speaker_asset_version_identity_update
BEFORE UPDATE OF
  organization_id, event_id, event_speaker_id, asset_id, generation, object_key,
  original_filename
ON speaker_asset_versions
BEGIN
  SELECT RAISE(ABORT, 'asset version identity is immutable');
END;

CREATE TRIGGER reject_agenda_room_track_conflict_insert
BEFORE INSERT ON agenda_items
WHEN EXISTS (
  SELECT 1 FROM agenda_items other
  WHERE other.organization_id=NEW.organization_id AND other.event_id=NEW.event_id
    AND other.revision_id=NEW.revision_id
    AND other.starts_at_ms<NEW.ends_at_ms AND other.ends_at_ms>NEW.starts_at_ms
    AND (
      other.room_id=NEW.room_id OR (
        NEW.track_id IS NOT NULL AND other.track_id=NEW.track_id
        AND EXISTS (SELECT 1 FROM event_tracks t WHERE t.id=NEW.track_id AND t.is_exclusive=1)
      )
    )
)
BEGIN
  SELECT RAISE(ABORT, 'agenda room or exclusive track conflict');
END;

CREATE TRIGGER reject_agenda_room_track_conflict_update
BEFORE UPDATE OF room_id,track_id,starts_at_ms,ends_at_ms,revision_id ON agenda_items
WHEN EXISTS (
  SELECT 1 FROM agenda_items other
  WHERE other.organization_id=NEW.organization_id AND other.event_id=NEW.event_id
    AND other.revision_id=NEW.revision_id AND other.id<>NEW.id
    AND other.starts_at_ms<NEW.ends_at_ms AND other.ends_at_ms>NEW.starts_at_ms
    AND (
      other.room_id=NEW.room_id OR (
        NEW.track_id IS NOT NULL AND other.track_id=NEW.track_id
        AND EXISTS (SELECT 1 FROM event_tracks t WHERE t.id=NEW.track_id AND t.is_exclusive=1)
      )
    )
)
BEGIN
  SELECT RAISE(ABORT, 'agenda room or exclusive track conflict');
END;

CREATE TRIGGER reject_agenda_speaker_conflict_insert
BEFORE INSERT ON agenda_item_speakers
WHEN EXISTS (
  SELECT 1 FROM agenda_items candidate
  JOIN agenda_items other ON other.revision_id=candidate.revision_id
    AND other.id<>candidate.id AND other.starts_at_ms<candidate.ends_at_ms
    AND other.ends_at_ms>candidate.starts_at_ms
  JOIN agenda_item_speakers existing ON existing.revision_id=other.revision_id
    AND existing.agenda_item_id=other.id
  WHERE candidate.id=NEW.agenda_item_id AND candidate.revision_id=NEW.revision_id
    AND existing.event_speaker_id=NEW.event_speaker_id
)
BEGIN
  SELECT RAISE(ABORT, 'agenda speaker conflict');
END;

CREATE TRIGGER reject_agenda_speaker_conflict_time_update
BEFORE UPDATE OF starts_at_ms,ends_at_ms,revision_id ON agenda_items
WHEN EXISTS (
  SELECT 1 FROM agenda_item_speakers moving
  JOIN agenda_items other ON other.revision_id=NEW.revision_id AND other.id<>NEW.id
    AND other.starts_at_ms<NEW.ends_at_ms AND other.ends_at_ms>NEW.starts_at_ms
  JOIN agenda_item_speakers existing ON existing.revision_id=other.revision_id
    AND existing.agenda_item_id=other.id
  WHERE moving.agenda_item_id=NEW.id AND moving.revision_id=OLD.revision_id
    AND existing.event_speaker_id=moving.event_speaker_id
)
BEGIN
  SELECT RAISE(ABORT, 'agenda speaker conflict');
END;

CREATE TRIGGER remove_revoked_active_role
AFTER UPDATE OF status ON user_roles
WHEN NEW.status='revoked'
BEGIN
  DELETE FROM session_active_roles
  WHERE user_id=NEW.user_id AND role=NEW.role;
END;

CREATE TRIGGER replace_revoked_default_account_role
AFTER UPDATE OF status ON user_roles
WHEN NEW.status='revoked' AND NEW.is_default=1
BEGIN
  UPDATE user_roles SET is_default=0,updated_at_ms=NEW.updated_at_ms
  WHERE user_id=NEW.user_id AND role=NEW.role;
  UPDATE user_roles SET is_default=1,updated_at_ms=NEW.updated_at_ms
  WHERE user_id=NEW.user_id AND role=(
    SELECT role FROM user_roles
    WHERE user_id=NEW.user_id AND status='active'
    ORDER BY CASE role WHEN 'organizer' THEN 1 WHEN 'reviewer' THEN 2 ELSE 3 END
    LIMIT 1
  );
END;

CREATE TRIGGER resource_access_grant_not_for_owner_insert
BEFORE INSERT ON resource_access_grants
WHEN EXISTS (
  SELECT 1 FROM owned_resources r
  WHERE r.id=NEW.resource_id AND r.owner_user_id=NEW.user_id
)
BEGIN
  SELECT RAISE(ABORT, 'resource owner does not need an access grant');
END;

CREATE TRIGGER resource_access_grant_not_for_owner_update
BEFORE UPDATE OF resource_id,user_id,status ON resource_access_grants
WHEN NEW.status='active' AND EXISTS (
  SELECT 1 FROM owned_resources r
  WHERE r.id=NEW.resource_id AND r.owner_user_id=NEW.user_id
)
BEGIN
  SELECT RAISE(ABORT, 'resource owner does not need an access grant');
END;

CREATE TRIGGER setup_credentials_cannot_be_changed_after_setup
BEFORE UPDATE ON instance_setup_credentials
WHEN EXISTS (
  SELECT 1 FROM instance_setup WHERE singleton_key = 'primary'
) OR EXISTS (
  SELECT 1 FROM organizations LIMIT 1
)
BEGIN
  SELECT RAISE(ABORT, 'instance setup has already completed');
END;

CREATE TRIGGER setup_credentials_cannot_be_created_after_setup
BEFORE INSERT ON instance_setup_credentials
WHEN EXISTS (
  SELECT 1 FROM instance_setup WHERE singleton_key = 'primary'
) OR EXISTS (
  SELECT 1 FROM organizations LIMIT 1
)
BEGIN
  SELECT RAISE(ABORT, 'instance setup has already completed');
END;

CREATE TRIGGER validate_accepted_session_decision
BEFORE INSERT ON accepted_sessions
WHEN NOT EXISTS (
  SELECT 1 FROM submission_decisions d
  WHERE d.id=NEW.decision_id AND d.organization_id=NEW.organization_id
    AND d.event_id=NEW.event_id AND d.submission_id=NEW.submission_id
    AND d.decision='accepted'
)
BEGIN
  SELECT RAISE(ABORT, 'accepted decision required');
END;

CREATE TRIGGER validate_accepted_session_decision_update
BEFORE UPDATE OF organization_id,event_id,submission_id,decision_id ON accepted_sessions
WHEN NOT EXISTS (
  SELECT 1 FROM submission_decisions d
  WHERE d.id=NEW.decision_id AND d.organization_id=NEW.organization_id
    AND d.event_id=NEW.event_id AND d.submission_id=NEW.submission_id
    AND d.decision='accepted'
)
BEGIN
  SELECT RAISE(ABORT, 'accepted decision required');
END;

CREATE TRIGGER validate_agenda_item_bounds_insert
BEFORE INSERT ON agenda_items
WHEN NOT EXISTS (
  SELECT 1 FROM events e JOIN schedule_revisions r
    ON r.organization_id=e.organization_id AND r.event_id=e.id
  WHERE e.organization_id=NEW.organization_id AND e.id=NEW.event_id
    AND r.id=NEW.revision_id AND r.status='draft'
    AND NEW.event_time_zone=e.time_zone
    AND NEW.starts_at_ms>=e.starts_at_ms AND NEW.ends_at_ms<=e.ends_at_ms
)
BEGIN
  SELECT RAISE(ABORT, 'agenda item outside draft event bounds');
END;

CREATE TRIGGER validate_agenda_item_bounds_update
BEFORE UPDATE ON agenda_items
WHEN NOT EXISTS (
  SELECT 1 FROM events e JOIN schedule_revisions r
    ON r.organization_id=e.organization_id AND r.event_id=e.id
  WHERE e.organization_id=NEW.organization_id AND e.id=NEW.event_id
    AND r.id=NEW.revision_id AND r.status='draft'
    AND NEW.event_time_zone=e.time_zone
    AND NEW.starts_at_ms>=e.starts_at_ms AND NEW.ends_at_ms<=e.ends_at_ms
)
BEGIN
  SELECT RAISE(ABORT, 'agenda item outside draft event bounds');
END;

CREATE TRIGGER validate_agenda_speaker_submission
BEFORE INSERT ON agenda_item_speakers
WHEN NOT EXISTS (
  SELECT 1 FROM agenda_items ai
  JOIN accepted_sessions ac ON ac.organization_id=ai.organization_id
    AND ac.event_id=ai.event_id AND ac.id=ai.accepted_session_id
  JOIN submission_speakers ss ON ss.organization_id=ac.organization_id
    AND ss.event_id=ac.event_id AND ss.submission_id=ac.submission_id
  WHERE ai.id=NEW.agenda_item_id AND ai.revision_id=NEW.revision_id
    AND ss.event_speaker_id=NEW.event_speaker_id
)
BEGIN
  SELECT RAISE(ABORT, 'agenda speaker must belong to accepted submission');
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
        UNION ALL
        SELECT 1 FROM evaluation_assignments a
        WHERE a.organization_id=NEW.organization_id AND a.event_id=NEW.event_id
          AND a.evaluator_user_id=NEW.user_id AND a.status!='revoked'
        UNION ALL
        SELECT 1 FROM identity_invitations i
        JOIN users u ON u.id=NEW.user_id
          AND u.normalized_email=NEW.normalized_email
          AND u.status='active'
        WHERE i.organization_id=NEW.organization_id AND i.event_id=NEW.event_id
          AND i.normalized_email=NEW.normalized_email
          AND i.role='evaluator' AND i.status='accepted'
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
        UNION ALL
        SELECT 1 FROM evaluation_assignments a
        WHERE a.organization_id=NEW.organization_id AND a.event_id=NEW.event_id
          AND a.evaluator_user_id=NEW.user_id AND a.status!='revoked'
        UNION ALL
        SELECT 1 FROM identity_invitations i
        JOIN users u ON u.id=NEW.user_id
          AND u.normalized_email=NEW.normalized_email
          AND u.status='active'
        WHERE i.organization_id=NEW.organization_id AND i.event_id=NEW.event_id
          AND i.normalized_email=NEW.normalized_email
          AND i.role='evaluator' AND i.status='accepted'
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

CREATE TRIGGER validate_communication_message_text_insert
BEFORE INSERT ON communication_messages
WHEN length(trim(NEW.subject)) NOT BETWEEN 1 AND 500
  OR length(NEW.html_body) NOT BETWEEN 1 AND 100000
BEGIN
  SELECT RAISE(ABORT, 'communication subject or body is invalid');
END;

CREATE TRIGGER validate_communication_message_text_update
BEFORE UPDATE OF subject,html_body ON communication_messages
WHEN length(trim(NEW.subject)) NOT BETWEEN 1 AND 500
  OR length(NEW.html_body) NOT BETWEEN 1 AND 100000
BEGIN
  SELECT RAISE(ABORT, 'communication subject or body is invalid');
END;

CREATE TRIGGER validate_communication_recipient_insert
BEFORE INSERT ON communication_messages
WHEN NEW.recipient_user_id IS NOT NULL AND NEW.event_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM organization_memberships m
  WHERE m.organization_id=NEW.organization_id AND m.user_id=NEW.recipient_user_id
    AND m.status='active'
  UNION ALL
  SELECT 1 FROM evaluation_assignments a
  WHERE a.organization_id=NEW.organization_id AND a.event_id=NEW.event_id
    AND a.evaluator_user_id=NEW.recipient_user_id AND a.status!='revoked'
  UNION ALL
  SELECT 1 FROM identity_invitations i
  JOIN users u ON u.id=NEW.recipient_user_id
    AND u.normalized_email=i.normalized_email
    AND u.status='active'
  WHERE i.organization_id=NEW.organization_id AND i.event_id=NEW.event_id
    AND i.normalized_email=NEW.recipient_email
    AND i.role='evaluator' AND i.status='accepted'
)
BEGIN
  SELECT RAISE(ABORT, 'communication recipient scope mismatch');
END;

CREATE TRIGGER validate_communication_recipient_update
BEFORE UPDATE OF organization_id,event_id,recipient_user_id,recipient_email
ON communication_messages
WHEN NEW.recipient_user_id IS NOT NULL AND NEW.event_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM organization_memberships m
  WHERE m.organization_id=NEW.organization_id AND m.user_id=NEW.recipient_user_id
    AND m.status='active'
  UNION ALL
  SELECT 1 FROM evaluation_assignments a
  WHERE a.organization_id=NEW.organization_id AND a.event_id=NEW.event_id
    AND a.evaluator_user_id=NEW.recipient_user_id AND a.status!='revoked'
  UNION ALL
  SELECT 1 FROM identity_invitations i
  JOIN users u ON u.id=NEW.recipient_user_id
    AND u.normalized_email=i.normalized_email
    AND u.status='active'
  WHERE i.organization_id=NEW.organization_id AND i.event_id=NEW.event_id
    AND i.normalized_email=NEW.recipient_email
    AND i.role='evaluator' AND i.status='accepted'
)
BEGIN
  SELECT RAISE(ABORT, 'communication recipient scope mismatch');
END;

CREATE TRIGGER validate_default_account_role_insert
BEFORE INSERT ON user_roles
WHEN NEW.is_default=1 AND NEW.status!='active'
BEGIN
  SELECT RAISE(ABORT, 'default role must be active');
END;

CREATE TRIGGER validate_default_account_role_update
BEFORE UPDATE OF is_default ON user_roles
WHEN NEW.is_default=1 AND NEW.status!='active'
BEGIN
  SELECT RAISE(ABORT, 'default role must be active');
END;

CREATE TRIGGER validate_draft_long_answers_insert
BEFORE INSERT ON submission_drafts
WHEN EXISTS (
  SELECT 1 FROM json_each(NEW.answers_json) AS answer
  JOIN call_for_speaker_forms AS form ON form.id=NEW.form_id
  JOIN json_each(form.schema_json, '$.fields') AS field
    ON json_extract(field.value, '$.key')=answer.key
  WHERE json_extract(field.value, '$.type')='textarea'
    AND length(CAST(answer.value AS TEXT)) > 5000
)
BEGIN
  SELECT RAISE(ABORT, 'textarea answer exceeds 5000 characters');
END;

CREATE TRIGGER validate_draft_long_answers_update
BEFORE UPDATE OF answers_json,form_id ON submission_drafts
WHEN EXISTS (
  SELECT 1 FROM json_each(NEW.answers_json) AS answer
  JOIN call_for_speaker_forms AS form ON form.id=NEW.form_id
  JOIN json_each(form.schema_json, '$.fields') AS field
    ON json_extract(field.value, '$.key')=answer.key
  WHERE json_extract(field.value, '$.type')='textarea'
    AND length(CAST(answer.value AS TEXT)) > 5000
)
BEGIN
  SELECT RAISE(ABORT, 'textarea answer exceeds 5000 characters');
END;

CREATE TRIGGER validate_evaluation_assignment_scope_insert
BEFORE INSERT ON evaluation_assignments
WHEN NOT EXISTS (
  SELECT 1 FROM evaluation_rounds r
  JOIN submissions s ON s.id=NEW.submission_id
  JOIN users u ON u.id=NEW.evaluator_user_id AND u.status='active'
  JOIN user_roles ur ON ur.user_id=u.id
    AND ur.role='reviewer' AND ur.status='active'
  JOIN identity_invitations i ON i.organization_id=NEW.organization_id
    AND i.event_id=NEW.event_id AND i.normalized_email=u.normalized_email
    AND i.role='evaluator' AND i.status='accepted'
  WHERE r.id=NEW.round_id AND r.organization_id=NEW.organization_id
    AND r.event_id=NEW.event_id AND s.organization_id=NEW.organization_id
    AND s.event_id=NEW.event_id
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
  JOIN users u ON u.id=NEW.evaluator_user_id AND u.status='active'
  JOIN user_roles ur ON ur.user_id=u.id
    AND ur.role='reviewer' AND ur.status='active'
  JOIN identity_invitations i ON i.organization_id=NEW.organization_id
    AND i.event_id=NEW.event_id AND i.normalized_email=u.normalized_email
    AND i.role='evaluator' AND i.status='accepted'
  WHERE r.id=NEW.round_id AND r.organization_id=NEW.organization_id
    AND r.event_id=NEW.event_id AND s.organization_id=NEW.organization_id
    AND s.event_id=NEW.event_id
)
BEGIN
  SELECT RAISE(ABORT, 'evaluation assignment scope mismatch');
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

CREATE TRIGGER validate_evaluation_guidance_insert
BEFORE INSERT ON evaluation_rounds
WHEN length(COALESCE(json_extract(NEW.rubric_json, '$.guidance'), '')) > 1000
BEGIN
  SELECT RAISE(ABORT, 'evaluation guidance exceeds 1000 characters');
END;

CREATE TRIGGER validate_evaluation_guidance_update
BEFORE UPDATE OF rubric_json ON evaluation_rounds
WHEN length(COALESCE(json_extract(NEW.rubric_json, '$.guidance'), '')) > 1000
BEGIN
  SELECT RAISE(ABORT, 'evaluation guidance exceeds 1000 characters');
END;

CREATE TRIGGER validate_evaluation_round_window_insert
BEFORE INSERT ON evaluation_rounds
WHEN NEW.review_opens_at_ms IS NOT NULL AND NEW.review_closes_at_ms IS NOT NULL
  AND NEW.review_closes_at_ms <= NEW.review_opens_at_ms
BEGIN
  SELECT RAISE(ABORT, 'review close must be after review open');
END;

CREATE TRIGGER validate_evaluation_round_window_update
BEFORE UPDATE OF review_opens_at_ms,review_closes_at_ms ON evaluation_rounds
WHEN NEW.review_opens_at_ms IS NOT NULL AND NEW.review_closes_at_ms IS NOT NULL
  AND NEW.review_closes_at_ms <= NEW.review_opens_at_ms
BEGIN
  SELECT RAISE(ABORT, 'review close must be after review open');
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

CREATE TRIGGER validate_event_branding_cover_insert
BEFORE INSERT ON events
WHEN NEW.cover_image_url LIKE '/api/v1/public/event-assets/%' AND NOT EXISTS (
  SELECT 1 FROM event_branding_assets a
  WHERE a.organization_id = NEW.organization_id AND a.kind = 'cover'
    AND a.asset_url = NEW.cover_image_url AND a.status IN ('pending', 'attached')
    AND (a.event_id IS NULL OR a.event_id = NEW.id)
)
BEGIN
  SELECT RAISE(ABORT, 'invalid event cover asset');
END;

CREATE TRIGGER validate_event_branding_cover_update
BEFORE UPDATE OF cover_image_url ON events
WHEN NEW.cover_image_url LIKE '/api/v1/public/event-assets/%' AND NOT EXISTS (
  SELECT 1 FROM event_branding_assets a
  WHERE a.organization_id = NEW.organization_id AND a.kind = 'cover'
    AND a.asset_url = NEW.cover_image_url AND a.status IN ('pending', 'attached')
    AND (a.event_id IS NULL OR a.event_id = NEW.id)
)
BEGIN
  SELECT RAISE(ABORT, 'invalid event cover asset');
END;

CREATE TRIGGER validate_event_branding_logo_insert
BEFORE INSERT ON events
WHEN NEW.logo_url LIKE '/api/v1/public/event-assets/%' AND NOT EXISTS (
  SELECT 1 FROM event_branding_assets a
  WHERE a.organization_id = NEW.organization_id AND a.kind = 'logo'
    AND a.asset_url = NEW.logo_url AND a.status IN ('pending', 'attached')
    AND (a.event_id IS NULL OR a.event_id = NEW.id)
)
BEGIN
  SELECT RAISE(ABORT, 'invalid event logo asset');
END;

CREATE TRIGGER validate_event_branding_logo_update
BEFORE UPDATE OF logo_url ON events
WHEN NEW.logo_url LIKE '/api/v1/public/event-assets/%' AND NOT EXISTS (
  SELECT 1 FROM event_branding_assets a
  WHERE a.organization_id = NEW.organization_id AND a.kind = 'logo'
    AND a.asset_url = NEW.logo_url AND a.status IN ('pending', 'attached')
    AND (a.event_id IS NULL OR a.event_id = NEW.id)
)
BEGIN
  SELECT RAISE(ABORT, 'invalid event logo asset');
END;

CREATE TRIGGER validate_event_details_insert
BEFORE INSERT ON events
WHEN NEW.location IS NULL OR length(trim(NEW.location)) NOT BETWEEN 1 AND 500
  OR NEW.description IS NULL OR length(trim(NEW.description)) NOT BETWEEN 1 AND 2000
BEGIN
  SELECT RAISE(ABORT, 'event location and description are invalid');
END;

CREATE TRIGGER validate_event_details_update
BEFORE UPDATE OF location,description ON events
WHEN NEW.location IS NULL OR length(trim(NEW.location)) NOT BETWEEN 1 AND 500
  OR NEW.description IS NULL OR length(trim(NEW.description)) NOT BETWEEN 1 AND 2000
BEGIN
  SELECT RAISE(ABORT, 'event location and description are invalid');
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

CREATE TRIGGER validate_password_recovery_email_insert
BEFORE INSERT ON password_recovery_challenges
WHEN NOT EXISTS (
  SELECT 1 FROM users u
  WHERE u.id=NEW.user_id AND u.normalized_email=NEW.normalized_email
    AND u.status='active' AND u.email_verified_at_ms IS NOT NULL
)
BEGIN
  SELECT RAISE(ABORT, 'password recovery identity mismatch');
END;

CREATE TRIGGER validate_password_recovery_email_update
BEFORE UPDATE OF user_id,normalized_email ON password_recovery_challenges
WHEN NOT EXISTS (
  SELECT 1 FROM users u
  WHERE u.id=NEW.user_id AND u.normalized_email=NEW.normalized_email
    AND u.status='active' AND u.email_verified_at_ms IS NOT NULL
)
BEGIN
  SELECT RAISE(ABORT, 'password recovery identity mismatch');
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

CREATE TRIGGER validate_session_active_role_user_insert
BEFORE INSERT ON session_active_roles
WHEN NOT EXISTS (
  SELECT 1 FROM sessions s
  JOIN user_roles r ON r.user_id=s.user_id AND r.role=NEW.role
  WHERE s.id=NEW.session_id AND s.user_id=NEW.user_id
    AND s.revoked_at_ms IS NULL AND r.status='active'
)
BEGIN
  SELECT RAISE(ABORT, 'active role is not available for session');
END;

CREATE TRIGGER validate_session_active_role_user_update
BEFORE UPDATE OF session_id,user_id,role ON session_active_roles
WHEN NOT EXISTS (
  SELECT 1 FROM sessions s
  JOIN user_roles r ON r.user_id=s.user_id AND r.role=NEW.role
  WHERE s.id=NEW.session_id AND s.user_id=NEW.user_id
    AND s.revoked_at_ms IS NULL AND r.status='active'
)
BEGIN
  SELECT RAISE(ABORT, 'active role is not available for session');
END;

CREATE TRIGGER validate_speaker_task_long_response
BEFORE UPDATE OF response_json ON speaker_tasks
WHEN NEW.response_json IS NOT NULL AND EXISTS (
  SELECT 1 FROM json_each(NEW.response_json) AS answer
  JOIN json_each(NEW.form_schema_json, '$.fields') AS field
    ON json_extract(field.value, '$.key')=answer.key
  WHERE json_extract(field.value, '$.type')='textarea'
    AND length(CAST(answer.value AS TEXT)) > 4000
)
BEGIN
  SELECT RAISE(ABORT, 'task textarea answer exceeds 4000 characters');
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

CREATE TRIGGER validate_submission_contributor_invitation_insert
BEFORE INSERT ON submission_contributors
WHEN (NEW.invitation_status='pending') !=
     (NEW.invitation_token_hash IS NOT NULL AND NEW.invitation_expires_at_ms IS NOT NULL
      AND NEW.invited_at_ms IS NOT NULL)
BEGIN
  SELECT RAISE(ABORT, 'invalid co-speaker invitation state');
END;

CREATE TRIGGER validate_submission_contributor_invitation_update
BEFORE UPDATE OF invitation_status,invitation_token_hash,invitation_expires_at_ms,invited_at_ms
ON submission_contributors
WHEN (NEW.invitation_status='pending') !=
     (NEW.invitation_token_hash IS NOT NULL AND NEW.invitation_expires_at_ms IS NOT NULL
      AND NEW.invited_at_ms IS NOT NULL)
BEGIN
  SELECT RAISE(ABORT, 'invalid co-speaker invitation state');
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

CREATE TRIGGER validate_submission_decision_scope_insert
BEFORE INSERT ON submission_decisions
WHEN NOT EXISTS (
  SELECT 1 FROM submissions s
  LEFT JOIN evaluation_rounds r ON r.id=NEW.round_id
  WHERE s.id=NEW.submission_id AND s.organization_id=NEW.organization_id
    AND s.event_id=NEW.event_id
    AND (NEW.round_id IS NULL OR (r.organization_id=NEW.organization_id
      AND r.event_id=NEW.event_id))
)
BEGIN
  SELECT RAISE(ABORT, 'submission decision scope mismatch');
END;

CREATE TRIGGER validate_submission_decision_scope_update
BEFORE UPDATE OF organization_id,event_id,round_id,submission_id ON submission_decisions
WHEN NOT EXISTS (
  SELECT 1 FROM submissions s
  LEFT JOIN evaluation_rounds r ON r.id=NEW.round_id
  WHERE s.id=NEW.submission_id AND s.organization_id=NEW.organization_id
    AND s.event_id=NEW.event_id
    AND (NEW.round_id IS NULL OR (r.organization_id=NEW.organization_id
      AND r.event_id=NEW.event_id))
)
BEGIN
  SELECT RAISE(ABORT, 'submission decision scope mismatch');
END;

CREATE TRIGGER validate_submission_draft_scope_insert
BEFORE INSERT ON submission_drafts
WHEN NOT EXISTS (
  SELECT 1 FROM call_for_speaker_forms f
  WHERE f.id=NEW.form_id AND f.organization_id=NEW.organization_id
    AND f.event_id=NEW.event_id
)
BEGIN
  SELECT RAISE(ABORT, 'submission draft scope mismatch');
END;

CREATE TRIGGER validate_submission_draft_scope_update
BEFORE UPDATE OF organization_id,event_id,form_id ON submission_drafts
WHEN NOT EXISTS (
  SELECT 1 FROM call_for_speaker_forms f
  WHERE f.id=NEW.form_id AND f.organization_id=NEW.organization_id
    AND f.event_id=NEW.event_id
)
BEGIN
  SELECT RAISE(ABORT, 'submission draft scope mismatch');
END;

CREATE TRIGGER validate_submission_form_scope_insert
BEFORE INSERT ON submissions
WHEN NOT EXISTS (
  SELECT 1 FROM call_for_speaker_forms f
  WHERE f.id=NEW.form_id AND f.organization_id=NEW.organization_id
    AND f.event_id=NEW.event_id
)
BEGIN
  SELECT RAISE(ABORT, 'submission form scope mismatch');
END;

CREATE TRIGGER validate_submission_form_scope_update
BEFORE UPDATE OF organization_id,event_id,form_id ON submissions
WHEN NOT EXISTS (
  SELECT 1 FROM call_for_speaker_forms f
  WHERE f.id=NEW.form_id AND f.organization_id=NEW.organization_id
    AND f.event_id=NEW.event_id
)
BEGIN
  SELECT RAISE(ABORT, 'submission form scope mismatch');
END;

CREATE TRIGGER validate_submission_long_answers_insert
BEFORE INSERT ON submissions
WHEN EXISTS (
  SELECT 1 FROM json_each(NEW.answers_json) AS answer
  JOIN call_for_speaker_forms AS form ON form.id=NEW.form_id
  JOIN json_each(form.schema_json, '$.fields') AS field
    ON json_extract(field.value, '$.key')=answer.key
  WHERE json_extract(field.value, '$.type')='textarea'
    AND length(CAST(answer.value AS TEXT)) > 5000
)
BEGIN
  SELECT RAISE(ABORT, 'textarea answer exceeds 5000 characters');
END;

CREATE TRIGGER validate_submission_long_answers_update
BEFORE UPDATE OF answers_json,form_id ON submissions
WHEN EXISTS (
  SELECT 1 FROM json_each(NEW.answers_json) AS answer
  JOIN call_for_speaker_forms AS form ON form.id=NEW.form_id
  JOIN json_each(form.schema_json, '$.fields') AS field
    ON json_extract(field.value, '$.key')=answer.key
  WHERE json_extract(field.value, '$.type')='textarea'
    AND length(CAST(answer.value AS TEXT)) > 5000
)
BEGIN
  SELECT RAISE(ABORT, 'textarea answer exceeds 5000 characters');
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

INSERT INTO instance_setup_credentials
  (singleton_key, deployment_key, generated_at_ms)
VALUES
  ('primary', lower(hex(randomblob(32))), unixepoch() * 1000);
-- Activity ingestion and projection pipeline.
CREATE TABLE activity_entities (
  public_id TEXT PRIMARY KEY NOT NULL,
  entity_type TEXT NOT NULL,
  internal_id TEXT NOT NULL,
  UNIQUE (entity_type,internal_id),
  CHECK (length(public_id)>1 AND substr(public_id,2) NOT GLOB '*[^0-9]*')
);

CREATE TABLE activities (
  id TEXT PRIMARY KEY NOT NULL,
  actor_type TEXT NOT NULL CHECK (actor_type IN ('user','system','anonymous')),
  actor_id TEXT,
  operation TEXT NOT NULL CHECK (operation IN ('create','read','update','delete')),
  resource_type TEXT NOT NULL,
  resource_id TEXT NOT NULL,
  occurred_at_ms INTEGER NOT NULL CHECK (occurred_at_ms >= 0),
  FOREIGN KEY (actor_id) REFERENCES activity_entities(public_id) ON DELETE RESTRICT,
  FOREIGN KEY (resource_id) REFERENCES activity_entities(public_id) ON DELETE RESTRICT,
  CHECK (substr(id,1,1)='A' AND length(id)>1
         AND substr(id,2) NOT GLOB '*[^0-9]*')
);

CREATE INDEX activities_order_idx ON activities(occurred_at_ms,id);

CREATE TABLE activity_status (
  activity_id TEXT PRIMARY KEY NOT NULL,
  status TEXT NOT NULL DEFAULT 'UNPROCESSED'
    CHECK (status IN ('UNPROCESSED','PROCESSING','PROCESSED','FAILED')),
  claim_token TEXT,
  claimed_at_ms INTEGER,
  queued_at_ms INTEGER,
  processed_at_ms INTEGER,
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  last_error_code TEXT,
  updated_at_ms INTEGER NOT NULL CHECK (updated_at_ms >= 0),
  FOREIGN KEY (activity_id) REFERENCES activities(id) ON DELETE RESTRICT,
  CHECK ((claim_token IS NULL) = (claimed_at_ms IS NULL))
);

CREATE INDEX activity_status_work_idx
  ON activity_status(status,queued_at_ms,claimed_at_ms,updated_at_ms,activity_id);

CREATE TABLE activity_distribution_guards (
  activity_id TEXT PRIMARY KEY NOT NULL,
  claim_token TEXT NOT NULL,
  FOREIGN KEY (activity_id) REFERENCES activities(id) ON DELETE CASCADE
);

CREATE TABLE activity_routing (
  activity_id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT,
  event_id TEXT,
  FOREIGN KEY (activity_id) REFERENCES activities(id) ON DELETE CASCADE,
  FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id,event_id)
    REFERENCES events(organization_id,id) ON DELETE RESTRICT
);

CREATE TABLE organization_activity (
  organization_id TEXT NOT NULL,
  activity_id TEXT NOT NULL,
  occurred_at_ms INTEGER NOT NULL CHECK (occurred_at_ms >= 0),
  PRIMARY KEY (organization_id,activity_id),
  FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT,
  FOREIGN KEY (activity_id) REFERENCES activities(id) ON DELETE RESTRICT
);

CREATE INDEX organization_activity_feed_idx
  ON organization_activity(organization_id,occurred_at_ms DESC,activity_id DESC);

CREATE TABLE event_activity (
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  activity_id TEXT NOT NULL,
  occurred_at_ms INTEGER NOT NULL CHECK (occurred_at_ms >= 0),
  PRIMARY KEY (event_id,activity_id),
  FOREIGN KEY (organization_id,event_id)
    REFERENCES events(organization_id,id) ON DELETE RESTRICT,
  FOREIGN KEY (activity_id) REFERENCES activities(id) ON DELETE RESTRICT
);

CREATE INDEX event_activity_feed_idx
  ON event_activity(event_id,occurred_at_ms DESC,activity_id DESC);

CREATE TABLE organizer_activity (
  user_id TEXT NOT NULL,
  activity_id TEXT NOT NULL,
  occurred_at_ms INTEGER NOT NULL CHECK (occurred_at_ms >= 0),
  PRIMARY KEY (user_id,activity_id),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT,
  FOREIGN KEY (activity_id) REFERENCES activities(id) ON DELETE RESTRICT
);

CREATE TABLE reviewer_activity (
  user_id TEXT NOT NULL,
  activity_id TEXT NOT NULL,
  occurred_at_ms INTEGER NOT NULL CHECK (occurred_at_ms >= 0),
  PRIMARY KEY (user_id,activity_id),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT,
  FOREIGN KEY (activity_id) REFERENCES activities(id) ON DELETE RESTRICT
);

CREATE TABLE speaker_activity (
  user_id TEXT NOT NULL,
  activity_id TEXT NOT NULL,
  occurred_at_ms INTEGER NOT NULL CHECK (occurred_at_ms >= 0),
  PRIMARY KEY (user_id,activity_id),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT,
  FOREIGN KEY (activity_id) REFERENCES activities(id) ON DELETE RESTRICT
);
