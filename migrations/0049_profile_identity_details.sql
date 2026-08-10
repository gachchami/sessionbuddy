PRAGMA foreign_keys = ON;

ALTER TABLE users ADD COLUMN description TEXT
  CHECK(description IS NULL OR length(description) <= 1000);
ALTER TABLE users ADD COLUMN website_url TEXT
  CHECK(website_url IS NULL OR length(website_url) <= 500);
ALTER TABLE users ADD COLUMN linkedin_url TEXT
  CHECK(linkedin_url IS NULL OR length(linkedin_url) <= 500);
ALTER TABLE users ADD COLUMN x_url TEXT
  CHECK(x_url IS NULL OR length(x_url) <= 500);

CREATE TABLE user_headshots (
  user_id TEXT PRIMARY KEY NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  object_key TEXT NOT NULL UNIQUE,
  content_type TEXT NOT NULL CHECK(content_type IN ('image/jpeg','image/png','image/webp')),
  byte_size INTEGER NOT NULL CHECK(byte_size BETWEEN 1 AND 5242880),
  checksum_sha256 BLOB NOT NULL CHECK(length(checksum_sha256)=32),
  updated_at_ms INTEGER NOT NULL
);
