PRAGMA foreign_keys = ON;

-- Dashboard and portal lists put undated work last. Index the exact expression
-- used by their stable cursor order so D1 does not sort tens of thousands of
-- task rows into a temporary B-tree.
CREATE INDEX idx_speaker_tasks_dashboard_state_deadline
  ON speaker_tasks(
    organization_id,
    event_id,
    state,
    (due_at_ms IS NULL),
    due_at_ms,
    id
  );

CREATE INDEX idx_speaker_tasks_dashboard_all_deadline
  ON speaker_tasks(
    organization_id,
    event_id,
    (due_at_ms IS NULL),
    due_at_ms,
    id
  );

CREATE INDEX idx_speaker_tasks_dashboard_type_state_deadline
  ON speaker_tasks(
    organization_id,
    event_id,
    task_type,
    state,
    (due_at_ms IS NULL),
    due_at_ms,
    id
  );

CREATE INDEX idx_speaker_tasks_dashboard_type_deadline
  ON speaker_tasks(
    organization_id,
    event_id,
    task_type,
    (due_at_ms IS NULL),
    due_at_ms,
    id
  );

CREATE INDEX idx_speaker_tasks_portal_state_deadline
  ON speaker_tasks(
    organization_id,
    event_id,
    event_speaker_id,
    state,
    (due_at_ms IS NULL),
    due_at_ms,
    id
  );
