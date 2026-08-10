ALTER TABLE communication_messages
ADD COLUMN attempt_limit INTEGER NOT NULL DEFAULT 12
CHECK (attempt_limit >= 12);
