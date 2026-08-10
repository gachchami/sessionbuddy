PRAGMA foreign_keys = ON;

-- Identity fields are independent so the same model can serve initial account
-- completion and later profile edits. display_name remains the derived public
-- label used by existing product surfaces.
ALTER TABLE users ADD COLUMN first_name TEXT
  CHECK(first_name IS NULL OR length(first_name) BETWEEN 1 AND 100);
ALTER TABLE users ADD COLUMN last_name TEXT
  CHECK(last_name IS NULL OR length(last_name) BETWEEN 1 AND 100);
