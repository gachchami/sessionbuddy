PRAGMA foreign_keys = ON;

CREATE TABLE cfp_form_write_guards (
  id TEXT PRIMARY KEY NOT NULL,
  form_id TEXT NOT NULL,
  applied_changes INTEGER NOT NULL CHECK (applied_changes = 1),
  created_at_ms INTEGER NOT NULL,
  FOREIGN KEY (form_id) REFERENCES call_for_speaker_forms(id) ON DELETE RESTRICT
);

CREATE INDEX idx_cfp_form_write_guards_form
  ON cfp_form_write_guards(form_id,created_at_ms DESC);
