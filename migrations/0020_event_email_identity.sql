ALTER TABLE events ADD COLUMN email_sender_name TEXT
  CHECK (email_sender_name IS NULL OR length(email_sender_name) BETWEEN 1 AND 200);

ALTER TABLE events ADD COLUMN email_reply_to TEXT
  CHECK (email_reply_to IS NULL OR length(email_reply_to) BETWEEN 3 AND 320);
