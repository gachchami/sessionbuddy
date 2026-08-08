PRAGMA foreign_keys = ON;

CREATE TABLE event_rooms (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  status TEXT NOT NULL CHECK (status IN ('active', 'archived')),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id, event_id)
    REFERENCES events(organization_id, id) ON DELETE RESTRICT,
  UNIQUE (organization_id, event_id, id),
  UNIQUE (organization_id, event_id, name)
);

CREATE TABLE event_tracks (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  is_exclusive INTEGER NOT NULL DEFAULT 0 CHECK (is_exclusive IN (0, 1)),
  status TEXT NOT NULL CHECK (status IN ('active', 'archived')),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id, event_id)
    REFERENCES events(organization_id, id) ON DELETE RESTRICT,
  UNIQUE (organization_id, event_id, id),
  UNIQUE (organization_id, event_id, name)
);

CREATE TABLE accepted_sessions (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  submission_id TEXT NOT NULL,
  decision_id TEXT NOT NULL,
  created_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id, event_id, submission_id)
    REFERENCES submissions(organization_id, event_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (decision_id) REFERENCES submission_decisions(id) ON DELETE RESTRICT,
  UNIQUE (organization_id, event_id, id),
  UNIQUE (organization_id, event_id, submission_id),
  UNIQUE (decision_id)
);

CREATE TRIGGER validate_accepted_session_decision
BEFORE INSERT ON accepted_sessions
WHEN NOT EXISTS (
  SELECT 1 FROM submission_decisions d
  WHERE d.id=NEW.decision_id AND d.organization_id=NEW.organization_id
    AND d.event_id=NEW.event_id AND d.submission_id=NEW.submission_id
    AND d.decision='accepted'
)
BEGIN
  SELECT RAISE(ABORT, 'accepted decision required');
END;

CREATE TRIGGER validate_accepted_session_decision_update
BEFORE UPDATE OF organization_id,event_id,submission_id,decision_id ON accepted_sessions
WHEN NOT EXISTS (
  SELECT 1 FROM submission_decisions d
  WHERE d.id=NEW.decision_id AND d.organization_id=NEW.organization_id
    AND d.event_id=NEW.event_id AND d.submission_id=NEW.submission_id
    AND d.decision='accepted'
)
BEGIN
  SELECT RAISE(ABORT, 'accepted decision required');
END;

CREATE TABLE schedule_revisions (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  revision_number INTEGER NOT NULL CHECK (revision_number >= 1),
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  status TEXT NOT NULL CHECK (status IN ('draft', 'published', 'superseded')),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_by_user_id TEXT NOT NULL,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  published_at_ms INTEGER,
  FOREIGN KEY (organization_id, event_id)
    REFERENCES events(organization_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  UNIQUE (organization_id, event_id, id),
  UNIQUE (organization_id, event_id, revision_number),
  CHECK (
    (status='draft' AND published_at_ms IS NULL)
    OR (status IN ('published','superseded') AND published_at_ms IS NOT NULL)
  )
);

CREATE UNIQUE INDEX uq_schedule_revision_draft
  ON schedule_revisions(organization_id, event_id) WHERE status='draft';
CREATE UNIQUE INDEX uq_schedule_revision_published
  ON schedule_revisions(organization_id, event_id) WHERE status='published';

CREATE TABLE agenda_items (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  accepted_session_id TEXT NOT NULL,
  room_id TEXT NOT NULL,
  track_id TEXT,
  event_date TEXT NOT NULL CHECK (
    length(event_date)=10 AND event_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
  ),
  event_time_zone TEXT NOT NULL,
  starts_at_ms INTEGER NOT NULL,
  ends_at_ms INTEGER NOT NULL,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id, event_id, revision_id)
    REFERENCES schedule_revisions(organization_id, event_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, event_id, accepted_session_id)
    REFERENCES accepted_sessions(organization_id, event_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, event_id, room_id)
    REFERENCES event_rooms(organization_id, event_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, event_id, track_id)
    REFERENCES event_tracks(organization_id, event_id, id) ON DELETE RESTRICT,
  UNIQUE (organization_id, event_id, revision_id, id),
  UNIQUE (revision_id, accepted_session_id),
  CHECK (starts_at_ms < ends_at_ms)
);

CREATE TABLE agenda_item_speakers (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  agenda_item_id TEXT NOT NULL,
  event_speaker_id TEXT NOT NULL,
  created_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id, event_id, revision_id, agenda_item_id)
    REFERENCES agenda_items(organization_id, event_id, revision_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, event_id, event_speaker_id)
    REFERENCES event_speakers(organization_id, event_id, id) ON DELETE RESTRICT,
  UNIQUE (revision_id, agenda_item_id, event_speaker_id)
);

-- A following statement inserts `changes()` here inside the same D1 batch. The
-- CHECK turns an optimistic UPDATE that matched zero rows into a transaction
-- failure, preventing stale requests from committing audit/idempotency rows.
CREATE TABLE agenda_write_guards (
  id TEXT PRIMARY KEY NOT NULL,
  agenda_item_id TEXT NOT NULL,
  applied_changes INTEGER NOT NULL CHECK (applied_changes = 1),
  created_at_ms INTEGER NOT NULL
);

CREATE TRIGGER validate_agenda_item_bounds_insert
BEFORE INSERT ON agenda_items
WHEN NOT EXISTS (
  SELECT 1 FROM events e JOIN schedule_revisions r
    ON r.organization_id=e.organization_id AND r.event_id=e.id
  WHERE e.organization_id=NEW.organization_id AND e.id=NEW.event_id
    AND r.id=NEW.revision_id AND r.status='draft'
    AND NEW.event_time_zone=e.time_zone
    AND NEW.starts_at_ms>=e.starts_at_ms AND NEW.ends_at_ms<=e.ends_at_ms
)
BEGIN
  SELECT RAISE(ABORT, 'agenda item outside draft event bounds');
END;

CREATE TRIGGER validate_agenda_item_bounds_update
BEFORE UPDATE ON agenda_items
WHEN NOT EXISTS (
  SELECT 1 FROM events e JOIN schedule_revisions r
    ON r.organization_id=e.organization_id AND r.event_id=e.id
  WHERE e.organization_id=NEW.organization_id AND e.id=NEW.event_id
    AND r.id=NEW.revision_id AND r.status='draft'
    AND NEW.event_time_zone=e.time_zone
    AND NEW.starts_at_ms>=e.starts_at_ms AND NEW.ends_at_ms<=e.ends_at_ms
)
BEGIN
  SELECT RAISE(ABORT, 'agenda item outside draft event bounds');
END;

CREATE TRIGGER reject_agenda_room_track_conflict_insert
BEFORE INSERT ON agenda_items
WHEN EXISTS (
  SELECT 1 FROM agenda_items other
  WHERE other.organization_id=NEW.organization_id AND other.event_id=NEW.event_id
    AND other.revision_id=NEW.revision_id
    AND other.starts_at_ms<NEW.ends_at_ms AND other.ends_at_ms>NEW.starts_at_ms
    AND (
      other.room_id=NEW.room_id OR (
        NEW.track_id IS NOT NULL AND other.track_id=NEW.track_id
        AND EXISTS (SELECT 1 FROM event_tracks t WHERE t.id=NEW.track_id AND t.is_exclusive=1)
      )
    )
)
BEGIN
  SELECT RAISE(ABORT, 'agenda room or exclusive track conflict');
END;

CREATE TRIGGER reject_agenda_room_track_conflict_update
BEFORE UPDATE OF room_id,track_id,starts_at_ms,ends_at_ms,revision_id ON agenda_items
WHEN EXISTS (
  SELECT 1 FROM agenda_items other
  WHERE other.organization_id=NEW.organization_id AND other.event_id=NEW.event_id
    AND other.revision_id=NEW.revision_id AND other.id<>NEW.id
    AND other.starts_at_ms<NEW.ends_at_ms AND other.ends_at_ms>NEW.starts_at_ms
    AND (
      other.room_id=NEW.room_id OR (
        NEW.track_id IS NOT NULL AND other.track_id=NEW.track_id
        AND EXISTS (SELECT 1 FROM event_tracks t WHERE t.id=NEW.track_id AND t.is_exclusive=1)
      )
    )
)
BEGIN
  SELECT RAISE(ABORT, 'agenda room or exclusive track conflict');
END;

CREATE TRIGGER validate_agenda_speaker_submission
BEFORE INSERT ON agenda_item_speakers
WHEN NOT EXISTS (
  SELECT 1 FROM agenda_items ai
  JOIN accepted_sessions ac ON ac.organization_id=ai.organization_id
    AND ac.event_id=ai.event_id AND ac.id=ai.accepted_session_id
  JOIN submission_speakers ss ON ss.organization_id=ac.organization_id
    AND ss.event_id=ac.event_id AND ss.submission_id=ac.submission_id
  WHERE ai.id=NEW.agenda_item_id AND ai.revision_id=NEW.revision_id
    AND ss.event_speaker_id=NEW.event_speaker_id
)
BEGIN
  SELECT RAISE(ABORT, 'agenda speaker must belong to accepted submission');
END;

CREATE TRIGGER reject_agenda_speaker_conflict_insert
BEFORE INSERT ON agenda_item_speakers
WHEN EXISTS (
  SELECT 1 FROM agenda_items candidate
  JOIN agenda_items other ON other.revision_id=candidate.revision_id
    AND other.id<>candidate.id AND other.starts_at_ms<candidate.ends_at_ms
    AND other.ends_at_ms>candidate.starts_at_ms
  JOIN agenda_item_speakers existing ON existing.revision_id=other.revision_id
    AND existing.agenda_item_id=other.id
  WHERE candidate.id=NEW.agenda_item_id AND candidate.revision_id=NEW.revision_id
    AND existing.event_speaker_id=NEW.event_speaker_id
)
BEGIN
  SELECT RAISE(ABORT, 'agenda speaker conflict');
END;

CREATE TRIGGER reject_agenda_speaker_conflict_time_update
BEFORE UPDATE OF starts_at_ms,ends_at_ms,revision_id ON agenda_items
WHEN EXISTS (
  SELECT 1 FROM agenda_item_speakers moving
  JOIN agenda_items other ON other.revision_id=NEW.revision_id AND other.id<>NEW.id
    AND other.starts_at_ms<NEW.ends_at_ms AND other.ends_at_ms>NEW.starts_at_ms
  JOIN agenda_item_speakers existing ON existing.revision_id=other.revision_id
    AND existing.agenda_item_id=other.id
  WHERE moving.agenda_item_id=NEW.id AND moving.revision_id=OLD.revision_id
    AND existing.event_speaker_id=moving.event_speaker_id
)
BEGIN
  SELECT RAISE(ABORT, 'agenda speaker conflict');
END;

CREATE TRIGGER prevent_published_agenda_item_delete
BEFORE DELETE ON agenda_items
WHEN EXISTS (SELECT 1 FROM schedule_revisions r WHERE r.id=OLD.revision_id AND r.status='published')
BEGIN
  SELECT RAISE(ABORT, 'published agenda revision is immutable');
END;

CREATE TRIGGER prevent_published_agenda_speaker_insert
BEFORE INSERT ON agenda_item_speakers
WHEN EXISTS (SELECT 1 FROM schedule_revisions r WHERE r.id=NEW.revision_id AND r.status='published')
BEGIN
  SELECT RAISE(ABORT, 'published agenda revision is immutable');
END;

CREATE TRIGGER prevent_published_agenda_speaker_delete
BEFORE DELETE ON agenda_item_speakers
WHEN EXISTS (SELECT 1 FROM schedule_revisions r WHERE r.id=OLD.revision_id AND r.status='published')
BEGIN
  SELECT RAISE(ABORT, 'published agenda revision is immutable');
END;

CREATE INDEX idx_agenda_items_revision_time
  ON agenda_items(organization_id,event_id,revision_id,starts_at_ms,id);
CREATE INDEX idx_agenda_items_room_time
  ON agenda_items(revision_id,room_id,starts_at_ms,ends_at_ms,id);
CREATE INDEX idx_agenda_items_track_time
  ON agenda_items(revision_id,track_id,starts_at_ms,ends_at_ms,id);
CREATE INDEX idx_agenda_speakers_conflict
  ON agenda_item_speakers(revision_id,event_speaker_id,agenda_item_id);
CREATE INDEX idx_accepted_sessions_event
  ON accepted_sessions(organization_id,event_id,created_at_ms DESC,id DESC);
