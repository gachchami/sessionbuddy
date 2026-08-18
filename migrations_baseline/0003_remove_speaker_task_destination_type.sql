-- speaker_tasks.task_type is the sole task-kind contract. Migration 0002
-- proved and enforced that destination_type carried the same value, so remove
-- the duplicate column rather than allowing a second meaning to emerge.
-- Back up data-bearing databases before application. Rollback is restore from
-- that backup because dropping the column is irreversible; recovery after a
-- failed migration must validate the two upload-contract triggers and the
-- speaker_tasks schema before retrying through the migration runner.

DROP TRIGGER speaker_task_upload_contract_insert;
DROP TRIGGER speaker_task_upload_contract_update;

ALTER TABLE speaker_tasks DROP COLUMN destination_type;

CREATE TRIGGER speaker_task_upload_contract_insert
BEFORE INSERT ON speaker_tasks
WHEN (
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
BEFORE UPDATE OF task_type,form_schema_json ON speaker_tasks
WHEN (
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
