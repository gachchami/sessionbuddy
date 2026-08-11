CREATE TABLE activity_entities (
  public_id TEXT PRIMARY KEY NOT NULL,
  entity_type TEXT NOT NULL,
  internal_id TEXT NOT NULL,
  UNIQUE (entity_type,internal_id),
  CHECK (length(public_id)>1 AND substr(public_id,2) NOT GLOB '*[^0-9]*')
);

CREATE TABLE activities (
  id TEXT PRIMARY KEY NOT NULL,
  actor_type TEXT NOT NULL CHECK (actor_type IN ('user','system','anonymous')),
  actor_id TEXT,
  operation TEXT NOT NULL CHECK (operation IN ('create','read','update','delete')),
  resource_type TEXT NOT NULL,
  resource_id TEXT NOT NULL,
  occurred_at_ms INTEGER NOT NULL CHECK (occurred_at_ms >= 0),
  FOREIGN KEY (actor_id) REFERENCES activity_entities(public_id) ON DELETE RESTRICT,
  FOREIGN KEY (resource_id) REFERENCES activity_entities(public_id) ON DELETE RESTRICT,
  CHECK (substr(id,1,1)='A' AND length(id)>1
         AND substr(id,2) NOT GLOB '*[^0-9]*')
);

CREATE INDEX activities_order_idx
  ON activities(occurred_at_ms,id);

CREATE TABLE activity_status (
  activity_id TEXT PRIMARY KEY NOT NULL,
  status TEXT NOT NULL DEFAULT 'UNPROCESSED'
    CHECK (status IN ('UNPROCESSED','PROCESSING','PROCESSED','FAILED')),
  claim_token TEXT,
  claimed_at_ms INTEGER,
  queued_at_ms INTEGER,
  processed_at_ms INTEGER,
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  last_error_code TEXT,
  updated_at_ms INTEGER NOT NULL CHECK (updated_at_ms >= 0),
  FOREIGN KEY (activity_id) REFERENCES activities(id) ON DELETE RESTRICT,
  CHECK ((claim_token IS NULL) = (claimed_at_ms IS NULL))
);

CREATE INDEX activity_status_work_idx
  ON activity_status(status,queued_at_ms,claimed_at_ms,updated_at_ms,activity_id);

-- A distributor inserts and removes this guard inside its projection batch.
-- The NOT NULL scalar-subquery insert makes a lost lease abort the whole batch.
CREATE TABLE activity_distribution_guards (
  activity_id TEXT PRIMARY KEY NOT NULL,
  claim_token TEXT NOT NULL,
  FOREIGN KEY (activity_id) REFERENCES activities(id) ON DELETE CASCADE
);

-- Routing facts are operational input to the distributor, not activity semantics.
CREATE TABLE activity_routing (
  activity_id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT,
  event_id TEXT,
  FOREIGN KEY (activity_id) REFERENCES activities(id) ON DELETE CASCADE,
  FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id,event_id)
    REFERENCES events(organization_id,id) ON DELETE RESTRICT
);

CREATE TABLE organization_activity (
  organization_id TEXT NOT NULL,
  activity_id TEXT NOT NULL,
  occurred_at_ms INTEGER NOT NULL CHECK (occurred_at_ms >= 0),
  PRIMARY KEY (organization_id,activity_id),
  FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT,
  FOREIGN KEY (activity_id) REFERENCES activities(id) ON DELETE RESTRICT
);

CREATE INDEX organization_activity_feed_idx
  ON organization_activity(organization_id,occurred_at_ms DESC,activity_id DESC);

CREATE TABLE event_activity (
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  activity_id TEXT NOT NULL,
  occurred_at_ms INTEGER NOT NULL CHECK (occurred_at_ms >= 0),
  PRIMARY KEY (event_id,activity_id),
  FOREIGN KEY (organization_id,event_id)
    REFERENCES events(organization_id,id) ON DELETE RESTRICT,
  FOREIGN KEY (activity_id) REFERENCES activities(id) ON DELETE RESTRICT
);

CREATE INDEX event_activity_feed_idx
  ON event_activity(event_id,occurred_at_ms DESC,activity_id DESC);

CREATE TABLE organizer_activity (
  user_id TEXT NOT NULL,
  activity_id TEXT NOT NULL,
  occurred_at_ms INTEGER NOT NULL CHECK (occurred_at_ms >= 0),
  PRIMARY KEY (user_id,activity_id),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT,
  FOREIGN KEY (activity_id) REFERENCES activities(id) ON DELETE RESTRICT
);

CREATE TABLE reviewer_activity (
  user_id TEXT NOT NULL,
  activity_id TEXT NOT NULL,
  occurred_at_ms INTEGER NOT NULL CHECK (occurred_at_ms >= 0),
  PRIMARY KEY (user_id,activity_id),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT,
  FOREIGN KEY (activity_id) REFERENCES activities(id) ON DELETE RESTRICT
);

CREATE TABLE speaker_activity (
  user_id TEXT NOT NULL,
  activity_id TEXT NOT NULL,
  occurred_at_ms INTEGER NOT NULL CHECK (occurred_at_ms >= 0),
  PRIMARY KEY (user_id,activity_id),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT,
  FOREIGN KEY (activity_id) REFERENCES activities(id) ON DELETE RESTRICT
);
