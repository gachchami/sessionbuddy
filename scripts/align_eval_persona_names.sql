-- Align eval-fixture reviewer/speaker identities with the names the eval specs look for.
--
-- Why this exists: the organizer-facing round progress view resolves a reviewer as
-- COALESCE(NULLIF(TRIM(u.display_name),''), u.email). Fixture accounts provisioned outside
-- the "Invite reviewer" dialog (seed SQL, direct signup) can land with a NULL display_name,
-- so the organizer sees "namohh.namaha+reviewer1" where the ABS and CFP specs expect
-- "Sam Whitfield" -- and an agent told to assign or verify a named reviewer cannot find them.
--
-- Idempotent: safe to re-run. Only fills identities that are missing or still showing the
-- email local-part, so a name the user set themselves is never overwritten.
--
-- Apply locally:
--   uv run pywrangler d1 execute DB --local --file scripts/align_eval_persona_names.sql
-- Apply to the development worker:
--   uv run pywrangler d1 execute DB --remote --env dev --file scripts/align_eval_persona_names.sql

UPDATE users
   SET display_name = 'Sam Whitfield',
       first_name = COALESCE(NULLIF(TRIM(first_name), ''), 'Sam'),
       last_name = COALESCE(NULLIF(TRIM(last_name), ''), 'Whitfield'),
       updated_at_ms = CAST(strftime('%s', 'now') AS INTEGER) * 1000
 WHERE normalized_email = 'namohh.namaha+reviewer1@gmail.com'
   AND (
        display_name IS NULL
     OR TRIM(display_name) = ''
     OR display_name = 'namohh.namaha+reviewer1'
     OR display_name = 'namohh.namaha+reviewer1@gmail.com'
   );

UPDATE users
   SET display_name = 'Priya Raman',
       first_name = COALESCE(NULLIF(TRIM(first_name), ''), 'Priya'),
       last_name = COALESCE(NULLIF(TRIM(last_name), ''), 'Raman'),
       updated_at_ms = CAST(strftime('%s', 'now') AS INTEGER) * 1000
 WHERE normalized_email = 'namohh.namaha+speaker1@gmail.com'
   AND (
        display_name IS NULL
     OR TRIM(display_name) = ''
     OR display_name = 'namohh.namaha+speaker1'
     OR display_name = 'namohh.namaha+speaker1@gmail.com'
   );

UPDATE users
   SET display_name = 'Marcus Okafor',
       first_name = COALESCE(NULLIF(TRIM(first_name), ''), 'Marcus'),
       last_name = COALESCE(NULLIF(TRIM(last_name), ''), 'Okafor'),
       updated_at_ms = CAST(strftime('%s', 'now') AS INTEGER) * 1000
 WHERE normalized_email = 'namohh.namaha+speaker2@gmail.com'
   AND (
        display_name IS NULL
     OR TRIM(display_name) = ''
     OR display_name = 'namohh.namaha+speaker2'
     OR display_name = 'namohh.namaha+speaker2@gmail.com'
   );

UPDATE users
   SET display_name = 'Jordan Alvarez',
       first_name = COALESCE(NULLIF(TRIM(first_name), ''), 'Jordan'),
       last_name = COALESCE(NULLIF(TRIM(last_name), ''), 'Alvarez'),
       updated_at_ms = CAST(strftime('%s', 'now') AS INTEGER) * 1000
 WHERE normalized_email = 'jordan.organizer@sbek-test.example.com'
   AND (display_name IS NULL OR TRIM(display_name) = '');

-- Verify: every eval persona should now report a human name.
SELECT normalized_email, display_name
  FROM users
 WHERE normalized_email IN (
        'jordan.organizer@sbek-test.example.com',
        'namohh.namaha+speaker1@gmail.com',
        'namohh.namaha+speaker2@gmail.com',
        'namohh.namaha+reviewer1@gmail.com'
      )
 ORDER BY normalized_email;
