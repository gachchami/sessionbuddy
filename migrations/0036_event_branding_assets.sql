-- Keep staged event branding uploads tenant-scoped and attach them to exactly
-- one event. The event continues exposing stable public URLs while this table
-- owns the R2 object relationship.

CREATE TABLE event_branding_assets (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT,
  kind TEXT NOT NULL CHECK (kind IN ('logo', 'cover')),
  object_key TEXT NOT NULL CHECK (
    length(object_key) BETWEEN 32 AND 1024
    AND object_key LIKE 'public/event-branding/%'
  ),
  asset_url TEXT NOT NULL CHECK (
    length(asset_url) BETWEEN 32 AND 2000
    AND asset_url LIKE '/api/v1/public/event-assets/%'
  ),
  content_type TEXT NOT NULL CHECK (
    content_type IN ('image/jpeg', 'image/png', 'image/webp')
  ),
  byte_size INTEGER NOT NULL CHECK (byte_size BETWEEN 1 AND 2097152),
  checksum_sha256 BLOB NOT NULL CHECK (length(checksum_sha256) = 32),
  status TEXT NOT NULL CHECK (status IN ('pending', 'attached', 'retired')),
  created_by_user_id TEXT NOT NULL,
  created_at_ms INTEGER NOT NULL,
  attached_at_ms INTEGER,
  FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, event_id)
    REFERENCES events(organization_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  UNIQUE (organization_id, id),
  UNIQUE (object_key),
  UNIQUE (asset_url),
  CHECK (
    (status = 'pending' AND event_id IS NULL AND attached_at_ms IS NULL)
    OR (status = 'attached' AND event_id IS NOT NULL AND attached_at_ms IS NOT NULL)
    OR (status = 'retired' AND event_id IS NOT NULL AND attached_at_ms IS NOT NULL)
  )
);

CREATE UNIQUE INDEX uq_event_branding_assets_current
  ON event_branding_assets(organization_id, event_id, kind)
  WHERE status = 'attached';

CREATE INDEX idx_event_branding_assets_pending
  ON event_branding_assets(organization_id, status, created_at_ms, id);

CREATE TRIGGER validate_event_branding_logo_insert
BEFORE INSERT ON events
WHEN NEW.logo_url LIKE '/api/v1/public/event-assets/%' AND NOT EXISTS (
  SELECT 1 FROM event_branding_assets a
  WHERE a.organization_id = NEW.organization_id AND a.kind = 'logo'
    AND a.asset_url = NEW.logo_url AND a.status IN ('pending', 'attached')
    AND (a.event_id IS NULL OR a.event_id = NEW.id)
)
BEGIN
  SELECT RAISE(ABORT, 'invalid event logo asset');
END;

CREATE TRIGGER validate_event_branding_cover_insert
BEFORE INSERT ON events
WHEN NEW.cover_image_url LIKE '/api/v1/public/event-assets/%' AND NOT EXISTS (
  SELECT 1 FROM event_branding_assets a
  WHERE a.organization_id = NEW.organization_id AND a.kind = 'cover'
    AND a.asset_url = NEW.cover_image_url AND a.status IN ('pending', 'attached')
    AND (a.event_id IS NULL OR a.event_id = NEW.id)
)
BEGIN
  SELECT RAISE(ABORT, 'invalid event cover asset');
END;

CREATE TRIGGER attach_event_branding_insert
AFTER INSERT ON events
BEGIN
  UPDATE event_branding_assets
  SET event_id = NEW.id, status = 'attached', attached_at_ms = NEW.created_at_ms
  WHERE organization_id = NEW.organization_id AND kind = 'logo'
    AND asset_url = NEW.logo_url AND status = 'pending' AND event_id IS NULL;
  UPDATE event_branding_assets
  SET event_id = NEW.id, status = 'attached', attached_at_ms = NEW.created_at_ms
  WHERE organization_id = NEW.organization_id AND kind = 'cover'
    AND asset_url = NEW.cover_image_url AND status = 'pending' AND event_id IS NULL;
END;

CREATE TRIGGER validate_event_branding_logo_update
BEFORE UPDATE OF logo_url ON events
WHEN NEW.logo_url LIKE '/api/v1/public/event-assets/%' AND NOT EXISTS (
  SELECT 1 FROM event_branding_assets a
  WHERE a.organization_id = NEW.organization_id AND a.kind = 'logo'
    AND a.asset_url = NEW.logo_url AND a.status IN ('pending', 'attached')
    AND (a.event_id IS NULL OR a.event_id = NEW.id)
)
BEGIN
  SELECT RAISE(ABORT, 'invalid event logo asset');
END;

CREATE TRIGGER validate_event_branding_cover_update
BEFORE UPDATE OF cover_image_url ON events
WHEN NEW.cover_image_url LIKE '/api/v1/public/event-assets/%' AND NOT EXISTS (
  SELECT 1 FROM event_branding_assets a
  WHERE a.organization_id = NEW.organization_id AND a.kind = 'cover'
    AND a.asset_url = NEW.cover_image_url AND a.status IN ('pending', 'attached')
    AND (a.event_id IS NULL OR a.event_id = NEW.id)
)
BEGIN
  SELECT RAISE(ABORT, 'invalid event cover asset');
END;

CREATE TRIGGER attach_event_branding_update
AFTER UPDATE OF logo_url, cover_image_url ON events
BEGIN
  UPDATE event_branding_assets
  SET status = 'retired'
  WHERE organization_id = OLD.organization_id AND event_id = OLD.id
    AND kind = 'logo' AND status = 'attached'
    AND COALESCE(OLD.logo_url, '') != COALESCE(NEW.logo_url, '');
  UPDATE event_branding_assets
  SET status = 'retired'
  WHERE organization_id = OLD.organization_id AND event_id = OLD.id
    AND kind = 'cover' AND status = 'attached'
    AND COALESCE(OLD.cover_image_url, '') != COALESCE(NEW.cover_image_url, '');
  UPDATE event_branding_assets
  SET event_id = NEW.id, status = 'attached', attached_at_ms = NEW.updated_at_ms
  WHERE organization_id = NEW.organization_id AND kind = 'logo'
    AND asset_url = NEW.logo_url AND status = 'pending' AND event_id IS NULL;
  UPDATE event_branding_assets
  SET event_id = NEW.id, status = 'attached', attached_at_ms = NEW.updated_at_ms
  WHERE organization_id = NEW.organization_id AND kind = 'cover'
    AND asset_url = NEW.cover_image_url AND status = 'pending' AND event_id IS NULL;
END;
