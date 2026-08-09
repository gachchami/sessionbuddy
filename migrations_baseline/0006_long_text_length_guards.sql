-- Bound user-authored long text at the D1 boundary. SQLite cannot add a CHECK
-- to the already-referenced events table without rebuilding it, so preserve the
-- existing trigger-based event invariant and tighten its description cap.

DROP TRIGGER validate_event_details_insert;
DROP TRIGGER validate_event_details_update;

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

-- Stored email bodies contain rendered HTML, so their database allowance is
-- intentionally larger than the 10,000-character manual-message input cap.
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

-- Schema-driven textareas are stored inside JSON. Mirror the API limits by
-- joining each answer to its published field definition.
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

-- Custom speaker-task answers and evaluation guidance are also JSON-backed.
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
