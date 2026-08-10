PRAGMA foreign_keys = ON;

ALTER TABLE users ADD COLUMN first_name TEXT
  CHECK(first_name IS NULL OR length(first_name) BETWEEN 1 AND 100);
ALTER TABLE users ADD COLUMN last_name TEXT
  CHECK(last_name IS NULL OR length(last_name) BETWEEN 1 AND 100);
