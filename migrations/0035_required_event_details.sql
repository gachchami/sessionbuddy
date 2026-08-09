-- Enforce mandatory event details at the D1 database boundary without
-- rebuilding the parent events table and disturbing its dependent tables.

CREATE TRIGGER validate_event_details_insert
BEFORE INSERT ON events
WHEN NEW.location IS NULL OR length(trim(NEW.location)) NOT BETWEEN 1 AND 500
  OR NEW.description IS NULL OR length(trim(NEW.description)) NOT BETWEEN 1 AND 5000
BEGIN
  SELECT RAISE(ABORT, 'event location and description are required');
END;

CREATE TRIGGER validate_event_details_update
BEFORE UPDATE OF location,description ON events
WHEN NEW.location IS NULL OR length(trim(NEW.location)) NOT BETWEEN 1 AND 500
  OR NEW.description IS NULL OR length(trim(NEW.description)) NOT BETWEEN 1 AND 5000
BEGIN
  SELECT RAISE(ABORT, 'event location and description are required');
END;
