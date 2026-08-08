CREATE TABLE instance_setup_credentials (
  singleton_key TEXT PRIMARY KEY NOT NULL CHECK (singleton_key = 'primary'),
  deployment_key TEXT NOT NULL CHECK (
    length(deployment_key) = 64
    AND deployment_key NOT GLOB '*[^0-9a-f]*'
  ),
  generated_at_ms INTEGER NOT NULL
);

INSERT INTO instance_setup_credentials
  (singleton_key, deployment_key, generated_at_ms)
VALUES
  ('primary', lower(hex(randomblob(32))), unixepoch() * 1000);

CREATE TABLE instance_setup (
  singleton_key TEXT PRIMARY KEY NOT NULL CHECK (singleton_key = 'primary'),
  completed_at_ms INTEGER NOT NULL
);

INSERT INTO instance_setup (singleton_key, completed_at_ms)
SELECT 'primary', MIN(created_at_ms)
FROM organizations
HAVING COUNT(*) > 0;
