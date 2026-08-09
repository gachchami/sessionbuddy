PRAGMA foreign_keys = ON;

ALTER TABLE evaluation_rounds ADD COLUMN review_opens_at_ms INTEGER;
ALTER TABLE evaluation_rounds ADD COLUMN review_closes_at_ms INTEGER;
ALTER TABLE evaluations ADD COLUMN criterion_scores_json TEXT NOT NULL DEFAULT '{}'
  CHECK (json_valid(criterion_scores_json));

CREATE INDEX idx_evaluation_rounds_review_window
  ON evaluation_rounds(status,review_opens_at_ms,review_closes_at_ms,event_id,program_id);

CREATE TRIGGER validate_evaluation_round_window_insert
BEFORE INSERT ON evaluation_rounds
WHEN NEW.review_opens_at_ms IS NOT NULL AND NEW.review_closes_at_ms IS NOT NULL
  AND NEW.review_closes_at_ms <= NEW.review_opens_at_ms
BEGIN
  SELECT RAISE(ABORT, 'review close must be after review open');
END;

CREATE TRIGGER validate_evaluation_round_window_update
BEFORE UPDATE OF review_opens_at_ms,review_closes_at_ms ON evaluation_rounds
WHEN NEW.review_opens_at_ms IS NOT NULL AND NEW.review_closes_at_ms IS NOT NULL
  AND NEW.review_closes_at_ms <= NEW.review_opens_at_ms
BEGIN
  SELECT RAISE(ABORT, 'review close must be after review open');
END;
