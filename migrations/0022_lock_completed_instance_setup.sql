CREATE TRIGGER instance_setup_completion_cannot_be_deleted
BEFORE DELETE ON instance_setup
BEGIN
  SELECT RAISE(ABORT, 'instance setup completion is permanent');
END;

CREATE TRIGGER instance_setup_completion_cannot_be_changed
BEFORE UPDATE ON instance_setup
BEGIN
  SELECT RAISE(ABORT, 'instance setup completion is permanent');
END;

CREATE TRIGGER setup_credentials_cannot_be_created_after_setup
BEFORE INSERT ON instance_setup_credentials
WHEN EXISTS (
  SELECT 1 FROM instance_setup WHERE singleton_key = 'primary'
) OR EXISTS (
  SELECT 1 FROM organizations LIMIT 1
)
BEGIN
  SELECT RAISE(ABORT, 'instance setup has already completed');
END;

CREATE TRIGGER setup_credentials_cannot_be_changed_after_setup
BEFORE UPDATE ON instance_setup_credentials
WHEN EXISTS (
  SELECT 1 FROM instance_setup WHERE singleton_key = 'primary'
) OR EXISTS (
  SELECT 1 FROM organizations LIMIT 1
)
BEGIN
  SELECT RAISE(ABORT, 'instance setup has already completed');
END;

CREATE TRIGGER consume_setup_credentials_after_completion
AFTER INSERT ON instance_setup
BEGIN
  DELETE FROM instance_setup_credentials WHERE singleton_key = 'primary';
END;

CREATE TRIGGER consume_setup_credentials_after_organization_creation
AFTER INSERT ON organizations
BEGIN
  DELETE FROM instance_setup_credentials WHERE singleton_key = 'primary';
END;

DELETE FROM instance_setup_credentials
WHERE EXISTS (
  SELECT 1 FROM instance_setup WHERE singleton_key = 'primary'
) OR EXISTS (
  SELECT 1 FROM organizations LIMIT 1
);
