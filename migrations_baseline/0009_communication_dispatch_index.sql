CREATE INDEX idx_messages_dispatch
ON communication_messages(status,updated_at_ms,id);
