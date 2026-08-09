PRAGMA foreign_keys = ON;

ALTER TABLE identity_invitations ADD COLUMN display_name TEXT NOT NULL DEFAULT ''
  CHECK(length(display_name) <= 200);
ALTER TABLE identity_invitations ADD COLUMN job_title TEXT NOT NULL DEFAULT ''
  CHECK(length(job_title) <= 200);
ALTER TABLE identity_invitations ADD COLUMN company TEXT NOT NULL DEFAULT ''
  CHECK(length(company) <= 200);
