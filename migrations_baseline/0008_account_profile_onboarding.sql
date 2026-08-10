ALTER TABLE users
ADD COLUMN profile_completed_at_ms INTEGER
CHECK (profile_completed_at_ms IS NULL OR profile_completed_at_ms > 0);

-- Existing installations already collected an administrator name during setup.
-- Only accounts created after this migration enter the first-login flow.
UPDATE users
SET profile_completed_at_ms=updated_at_ms
WHERE display_name IS NOT NULL AND length(trim(display_name)) > 0;
