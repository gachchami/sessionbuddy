-- Add speaker/organizer asset discussions and content-level task idempotency.
-- Historical tasks deliberately retain a NULL fingerprint: the migration cannot
-- reconstruct the exact normalized request body that produced them.

CREATE UNIQUE INDEX uq_speaker_asset_versions_asset_identity
  ON speaker_asset_versions(organization_id,event_id,asset_id,id);

CREATE TABLE speaker_asset_comments (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  version_id TEXT NOT NULL,
  author_user_id TEXT NOT NULL,
  parent_comment_id TEXT,
  body_text TEXT NOT NULL CHECK(length(trim(body_text)) BETWEEN 1 AND 5000),
  visibility TEXT NOT NULL DEFAULT 'internal'
    CHECK (visibility IN ('internal', 'shared')),
  created_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id,event_id,asset_id)
    REFERENCES speaker_assets(organization_id,event_id,id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id,event_id,asset_id,version_id)
    REFERENCES speaker_asset_versions(organization_id,event_id,asset_id,id) ON DELETE RESTRICT,
  FOREIGN KEY (author_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id,event_id,asset_id,version_id,parent_comment_id)
    REFERENCES speaker_asset_comments(
      organization_id,event_id,asset_id,version_id,id
    ) ON DELETE RESTRICT,
  UNIQUE (organization_id,event_id,id),
  UNIQUE (organization_id,event_id,asset_id,version_id,id)
);

CREATE INDEX idx_speaker_asset_comments_version
  ON speaker_asset_comments(organization_id,event_id,version_id,created_at_ms,id);

CREATE TRIGGER trg_speaker_asset_comments_immutable
BEFORE UPDATE ON speaker_asset_comments
BEGIN
  SELECT RAISE(ABORT, 'speaker asset comments are immutable');
END;

ALTER TABLE speaker_tasks ADD COLUMN content_fingerprint BLOB;

-- Only the exact acceptance-generated task templates are system work. Give
-- every historical organizer-authored task a deterministic per-row fingerprint
-- so the system indexes below cannot reinterpret a legitimate custom request.
UPDATE speaker_tasks
SET content_fingerprint=CAST('legacy:' || id AS BLOB)
WHERE NOT (
  event_speaker_id IS NOT NULL
  AND form_schema_json IS NULL
  AND response_json IS NULL
  AND responded_at_ms IS NULL
  AND (
    (task_type='profile' AND destination_type='profile'
      AND title='Add your speaker biography'
      AND help_text='Your registration is complete; add the missing biography for the program.')
    OR
    (task_type='headshot' AND destination_type='headshot'
      AND title='Upload your headshot' AND help_text='Add a program-ready profile photo.')
    OR
    (task_type='slides' AND destination_type='slides'
      AND title='Upload your presentation'
      AND help_text='Share the final slide deck with the event team.')
  )
);

-- Preserve every historical row while closing redundant open system work.
-- The oldest task remains actionable; later duplicates become waived rather
-- than being deleted. Organizer-authored tasks were fingerprinted above and
-- are therefore outside these sets.
WITH ranked AS (
  SELECT id,ROW_NUMBER() OVER (
    PARTITION BY organization_id,event_id,event_speaker_id,task_type
    ORDER BY created_at_ms,id
  ) AS position
  FROM speaker_tasks
  WHERE state='open' AND content_fingerprint IS NULL
    AND event_speaker_id IS NOT NULL AND task_type IN ('profile','headshot')
)
UPDATE speaker_tasks
SET state='waived',waived_at_ms=updated_at_ms,version=version+1
WHERE id IN (SELECT id FROM ranked WHERE position>1);

WITH ranked AS (
  SELECT id,ROW_NUMBER() OVER (
    PARTITION BY organization_id,event_id,event_speaker_id,submission_id
    ORDER BY created_at_ms,id
  ) AS position
  FROM speaker_tasks
  WHERE state='open' AND content_fingerprint IS NULL
    AND event_speaker_id IS NOT NULL AND submission_id IS NOT NULL
    AND task_type='slides'
)
UPDATE speaker_tasks
SET state='waived',waived_at_ms=updated_at_ms,version=version+1
WHERE id IN (SELECT id FROM ranked WHERE position>1);

CREATE UNIQUE INDEX uq_speaker_tasks_open_system_identity
  ON speaker_tasks(organization_id,event_id,event_speaker_id,task_type)
  WHERE state='open' AND content_fingerprint IS NULL
    AND event_speaker_id IS NOT NULL AND task_type IN ('profile','headshot');

CREATE UNIQUE INDEX uq_speaker_tasks_open_system_slides
  ON speaker_tasks(organization_id,event_id,event_speaker_id,submission_id)
  WHERE state='open' AND content_fingerprint IS NULL
    AND event_speaker_id IS NOT NULL AND submission_id IS NOT NULL
    AND task_type='slides';

CREATE UNIQUE INDEX uq_speaker_tasks_open_content
  ON speaker_tasks(organization_id,event_id,content_fingerprint)
  WHERE state='open' AND content_fingerprint IS NOT NULL;
