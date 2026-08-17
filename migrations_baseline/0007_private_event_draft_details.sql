-- Private event drafts may be saved before their location and description are known.
-- Active and archived events retain the existing non-blank content invariant, and
-- including status in the update trigger prevents an incomplete draft from being
-- activated by any writer that bypasses the application route.
DROP TRIGGER IF EXISTS validate_event_details_insert;
DROP TRIGGER IF EXISTS validate_event_details_update;

ALTER TABLE events ADD COLUMN draft_starts_at_ms INTEGER;
ALTER TABLE events ADD COLUMN draft_ends_at_ms INTEGER;
ALTER TABLE events ADD COLUMN draft_delivery_mode TEXT
  CHECK (draft_delivery_mode IS NULL OR draft_delivery_mode IN ('in_person','virtual','hybrid'));

-- Before this migration, every draft stored complete event details in the
-- released columns. Preserve those values when readers begin preferring the
-- explicit draft projection.
UPDATE events
SET draft_starts_at_ms=starts_at_ms,
    draft_ends_at_ms=ends_at_ms,
    draft_delivery_mode=delivery_mode
WHERE status='draft';

CREATE TRIGGER validate_event_draft_time_insert
BEFORE INSERT ON events
WHEN (NEW.draft_starts_at_ms IS NOT NULL AND NEW.draft_starts_at_ms < 0)
  OR (NEW.draft_ends_at_ms IS NOT NULL AND NEW.draft_ends_at_ms < 0)
  OR (NEW.draft_starts_at_ms IS NOT NULL AND NEW.draft_ends_at_ms IS NOT NULL
      AND NEW.draft_ends_at_ms <= NEW.draft_starts_at_ms)
BEGIN
  SELECT RAISE(ABORT, 'event draft end must be after start');
END;

CREATE TRIGGER validate_event_draft_time_update
BEFORE UPDATE OF draft_starts_at_ms,draft_ends_at_ms ON events
WHEN (NEW.draft_starts_at_ms IS NOT NULL AND NEW.draft_starts_at_ms < 0)
  OR (NEW.draft_ends_at_ms IS NOT NULL AND NEW.draft_ends_at_ms < 0)
  OR (NEW.draft_starts_at_ms IS NOT NULL AND NEW.draft_ends_at_ms IS NOT NULL
      AND NEW.draft_ends_at_ms <= NEW.draft_starts_at_ms)
BEGIN
  SELECT RAISE(ABORT, 'event draft end must be after start');
END;

CREATE TRIGGER validate_event_draft_projection_insert
BEFORE INSERT ON events
WHEN NEW.status='draft' AND (
  (NEW.draft_starts_at_ms IS NOT NULL AND NEW.draft_ends_at_ms IS NOT NULL
   AND NEW.draft_delivery_mode IS NOT NULL
   AND NEW.location IS NOT NULL AND length(trim(NEW.location)) BETWEEN 1 AND 500
   AND NEW.description IS NOT NULL AND length(trim(NEW.description)) BETWEEN 1 AND 2000
   AND (
     NEW.starts_at_ms IS NOT NEW.draft_starts_at_ms
     OR NEW.ends_at_ms IS NOT NEW.draft_ends_at_ms
     OR NEW.delivery_mode IS NOT NEW.draft_delivery_mode
   ))
  OR ((NEW.draft_starts_at_ms IS NULL OR NEW.draft_ends_at_ms IS NULL
       OR NEW.draft_delivery_mode IS NULL
       OR NEW.location IS NULL OR length(trim(NEW.location)) NOT BETWEEN 1 AND 500
       OR NEW.description IS NULL OR length(trim(NEW.description)) NOT BETWEEN 1 AND 2000) AND (
     NEW.starts_at_ms != 0 OR NEW.ends_at_ms != 1 OR NEW.delivery_mode != 'in_person'
  ))
)
BEGIN
  SELECT RAISE(ABORT, 'event draft storage projection mismatch');
END;

CREATE TRIGGER validate_event_draft_projection_update
BEFORE UPDATE OF status,starts_at_ms,ends_at_ms,delivery_mode,location,description,
  draft_starts_at_ms,draft_ends_at_ms,draft_delivery_mode ON events
WHEN NEW.status='draft' AND (
  (NEW.draft_starts_at_ms IS NOT NULL AND NEW.draft_ends_at_ms IS NOT NULL
   AND NEW.draft_delivery_mode IS NOT NULL
   AND NEW.location IS NOT NULL AND length(trim(NEW.location)) BETWEEN 1 AND 500
   AND NEW.description IS NOT NULL AND length(trim(NEW.description)) BETWEEN 1 AND 2000
   AND (
     NEW.starts_at_ms IS NOT NEW.draft_starts_at_ms
     OR NEW.ends_at_ms IS NOT NEW.draft_ends_at_ms
     OR NEW.delivery_mode IS NOT NEW.draft_delivery_mode
   ))
  OR ((NEW.draft_starts_at_ms IS NULL OR NEW.draft_ends_at_ms IS NULL
       OR NEW.draft_delivery_mode IS NULL
       OR NEW.location IS NULL OR length(trim(NEW.location)) NOT BETWEEN 1 AND 500
       OR NEW.description IS NULL OR length(trim(NEW.description)) NOT BETWEEN 1 AND 2000) AND (
     NEW.starts_at_ms != 0 OR NEW.ends_at_ms != 1 OR NEW.delivery_mode != 'in_person'
  ))
)
BEGIN
  SELECT RAISE(ABORT, 'event draft storage projection mismatch');
END;

CREATE TRIGGER validate_event_details_insert
BEFORE INSERT ON events
WHEN NEW.status != 'draft' AND (
  (NEW.starts_at_ms = 0 AND NEW.ends_at_ms = 1)
  OR NEW.draft_starts_at_ms IS NOT NULL OR NEW.draft_ends_at_ms IS NOT NULL
  OR NEW.draft_delivery_mode IS NOT NULL
  OR NEW.location IS NULL OR length(trim(NEW.location)) NOT BETWEEN 1 AND 500
  OR NEW.description IS NULL OR length(trim(NEW.description)) NOT BETWEEN 1 AND 2000
)
BEGIN
  SELECT RAISE(ABORT, 'event details are incomplete or invalid');
END;

CREATE TRIGGER validate_event_details_update
BEFORE UPDATE OF starts_at_ms,ends_at_ms,delivery_mode,location,description,status,
  draft_starts_at_ms,draft_ends_at_ms,draft_delivery_mode ON events
WHEN NEW.status != 'draft' AND (
  (NEW.starts_at_ms = 0 AND NEW.ends_at_ms = 1 AND (
    OLD.status = 'draft' OR (OLD.status != 'active' AND NEW.status = 'active')
  ))
  OR NEW.draft_starts_at_ms IS NOT NULL OR NEW.draft_ends_at_ms IS NOT NULL
  OR NEW.draft_delivery_mode IS NOT NULL
  OR NEW.location IS NULL OR length(trim(NEW.location)) NOT BETWEEN 1 AND 500
  OR NEW.description IS NULL OR length(trim(NEW.description)) NOT BETWEEN 1 AND 2000
)
BEGIN
  SELECT RAISE(ABORT, 'event details are incomplete or invalid');
END;
