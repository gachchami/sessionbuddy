-- Repair system-created file tasks and enforce the upload contract already
-- required by the organizer task API. Back up data-bearing databases before
-- application. Rollback is restore-from-backup: the backfill is safe to keep,
-- but removing these guards would reopen creation of unusable task rows.

DROP TABLE IF EXISTS speaker_task_contract_migration_guard;
CREATE TABLE speaker_task_contract_migration_guard (
  mismatched_rows INTEGER NOT NULL CHECK (mismatched_rows = 0)
);

INSERT INTO speaker_task_contract_migration_guard (mismatched_rows)
SELECT COUNT(*) FROM speaker_tasks WHERE task_type != destination_type;

DROP TABLE speaker_task_contract_migration_guard;

DROP TABLE IF EXISTS speaker_task_contract_migration_clock;
CREATE TABLE speaker_task_contract_migration_clock (
  repaired_at_ms INTEGER NOT NULL
);
INSERT INTO speaker_task_contract_migration_clock (repaired_at_ms)
VALUES (unixepoch('now') * 1000);

UPDATE speaker_tasks
SET form_schema_json = CASE task_type
  WHEN 'headshot' THEN json_object(
    'fields', json_array(),
    'upload', json_object(
      'enabled', json('true'),
      'allowed_content_types', json_array('image/jpeg','image/png','image/webp'),
      'max_file_bytes', 5242880
    )
  )
  WHEN 'slides' THEN json_object(
    'fields', json_array(),
    'upload', json_object(
      'enabled', json('true'),
      'allowed_content_types', json_array(
        'application/pdf',
        'application/vnd.ms-powerpoint',
        'application/vnd.oasis.opendocument.presentation',
        'application/vnd.openxmlformats-officedocument.presentationml.presentation'
      ),
      'max_file_bytes', 52428800
    )
  )
  WHEN 'supporting_document' THEN json_object(
    'fields', json_array(),
    'upload', json_object(
      'enabled', json('true'),
      'allowed_content_types', json_array('application/pdf'),
      'max_file_bytes', 20971520
    )
  )
  ELSE form_schema_json
END,
updated_at_ms = MAX(
  updated_at_ms + 1,
  (SELECT repaired_at_ms FROM speaker_task_contract_migration_clock)
),
version = version + 1
WHERE task_type IN ('headshot','slides','supporting_document')
  AND (
    form_schema_json IS NULL
    OR json_extract(form_schema_json, '$.upload.enabled') IS NOT 1
    OR json_type(form_schema_json, '$.upload.allowed_content_types') != 'array'
    OR COALESCE(json_array_length(form_schema_json, '$.upload.allowed_content_types'), 0) = 0
    OR json_type(form_schema_json, '$.upload.max_file_bytes') != 'integer'
    OR COALESCE(json_extract(form_schema_json, '$.upload.max_file_bytes'), 0) <= 0
    OR json_extract(form_schema_json, '$.upload.max_file_bytes') > 52428800
    OR EXISTS (
      SELECT 1 FROM json_each(form_schema_json, '$.upload.allowed_content_types') item
      WHERE item.type != 'text' OR item.value = '' OR item.value NOT LIKE '%/%'
        OR item.value != lower(item.value)
    )
  );

DROP TABLE speaker_task_contract_migration_clock;

-- Refuse to install the enforcement triggers unless the repair left every
-- existing file task compliant. The guard is restartable after a failed run.
DROP TABLE IF EXISTS speaker_task_contract_migration_guard;
CREATE TABLE speaker_task_contract_migration_guard (
  invalid_rows INTEGER NOT NULL CHECK (invalid_rows = 0)
);

INSERT INTO speaker_task_contract_migration_guard (invalid_rows)
SELECT COUNT(*)
FROM speaker_tasks
WHERE task_type IN ('headshot','slides','supporting_document')
  AND (
    form_schema_json IS NULL
    OR json_extract(form_schema_json, '$.upload.enabled') IS NOT 1
    OR json_type(form_schema_json, '$.upload.allowed_content_types') != 'array'
    OR COALESCE(json_array_length(form_schema_json, '$.upload.allowed_content_types'), 0) = 0
    OR json_type(form_schema_json, '$.upload.max_file_bytes') != 'integer'
    OR COALESCE(json_extract(form_schema_json, '$.upload.max_file_bytes'), 0) <= 0
    OR json_extract(form_schema_json, '$.upload.max_file_bytes') > 52428800
    OR EXISTS (
      SELECT 1 FROM json_each(form_schema_json, '$.upload.allowed_content_types') item
      WHERE item.type != 'text' OR item.value = '' OR item.value NOT LIKE '%/%'
        OR item.value != lower(item.value)
    )
  );

DROP TABLE speaker_task_contract_migration_guard;

CREATE TRIGGER speaker_task_upload_contract_insert
BEFORE INSERT ON speaker_tasks
WHEN NEW.task_type != NEW.destination_type
  OR (
    NEW.task_type IN ('headshot','slides','supporting_document')
    AND (
      NEW.form_schema_json IS NULL
      OR json_extract(NEW.form_schema_json, '$.upload.enabled') IS NOT 1
      OR json_type(NEW.form_schema_json, '$.upload.allowed_content_types') != 'array'
      OR COALESCE(json_array_length(NEW.form_schema_json, '$.upload.allowed_content_types'), 0) = 0
      OR json_type(NEW.form_schema_json, '$.upload.max_file_bytes') != 'integer'
      OR COALESCE(json_extract(NEW.form_schema_json, '$.upload.max_file_bytes'), 0) <= 0
      OR json_extract(NEW.form_schema_json, '$.upload.max_file_bytes') > 52428800
      OR EXISTS (
        SELECT 1 FROM json_each(NEW.form_schema_json, '$.upload.allowed_content_types') item
        WHERE item.type != 'text' OR item.value = '' OR item.value NOT LIKE '%/%'
          OR item.value != lower(item.value)
      )
    )
  )
  OR (
    NEW.task_type NOT IN ('headshot','slides','supporting_document')
    AND (
      json_extract(NEW.form_schema_json, '$.upload.enabled') = 1
      OR COALESCE(json_array_length(NEW.form_schema_json, '$.upload.allowed_content_types'), 0) > 0
      OR json_extract(NEW.form_schema_json, '$.upload.max_file_bytes') IS NOT NULL
    )
  )
BEGIN
  SELECT RAISE(ABORT, 'speaker task upload contract invalid');
END;

CREATE TRIGGER speaker_task_upload_contract_update
BEFORE UPDATE OF task_type,destination_type,form_schema_json ON speaker_tasks
WHEN NEW.task_type != NEW.destination_type
  OR (
    NEW.task_type IN ('headshot','slides','supporting_document')
    AND (
      NEW.form_schema_json IS NULL
      OR json_extract(NEW.form_schema_json, '$.upload.enabled') IS NOT 1
      OR json_type(NEW.form_schema_json, '$.upload.allowed_content_types') != 'array'
      OR COALESCE(json_array_length(NEW.form_schema_json, '$.upload.allowed_content_types'), 0) = 0
      OR json_type(NEW.form_schema_json, '$.upload.max_file_bytes') != 'integer'
      OR COALESCE(json_extract(NEW.form_schema_json, '$.upload.max_file_bytes'), 0) <= 0
      OR json_extract(NEW.form_schema_json, '$.upload.max_file_bytes') > 52428800
      OR EXISTS (
        SELECT 1 FROM json_each(NEW.form_schema_json, '$.upload.allowed_content_types') item
        WHERE item.type != 'text' OR item.value = '' OR item.value NOT LIKE '%/%'
          OR item.value != lower(item.value)
      )
    )
  )
  OR (
    NEW.task_type NOT IN ('headshot','slides','supporting_document')
    AND (
      json_extract(NEW.form_schema_json, '$.upload.enabled') = 1
      OR COALESCE(json_array_length(NEW.form_schema_json, '$.upload.allowed_content_types'), 0) > 0
      OR json_extract(NEW.form_schema_json, '$.upload.max_file_bytes') IS NOT NULL
    )
  )
BEGIN
  SELECT RAISE(ABORT, 'speaker task upload contract invalid');
END;
