-- Support the speaker portal's participant-first accepted-session projection.
-- The existing UNIQUE indexes begin with accepted_session_id and cannot serve
-- a lookup that starts from the authenticated event_speaker_id.
CREATE INDEX idx_accepted_session_participants_speaker
  ON accepted_session_participants(
    organization_id,event_id,event_speaker_id,accepted_session_id
  )
  WHERE event_speaker_id IS NOT NULL;
