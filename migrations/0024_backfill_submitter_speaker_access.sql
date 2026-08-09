PRAGMA foreign_keys = ON;

-- Authenticated submitters created before CFP-scoped sign-in provisioning was
-- corrected already have a user, person, and event-speaker record. Grant only
-- the missing active speaker role; preserve any explicitly revoked role.
INSERT INTO event_memberships
  (id,organization_id,event_id,user_id,role,status,created_at_ms,updated_at_ms)
SELECT lower(hex(randomblob(16))),s.organization_id,s.event_id,s.submitter_user_id,
       'speaker','active',MIN(s.created_at_ms),MAX(s.updated_at_ms)
FROM submissions s
JOIN organization_memberships om
  ON om.organization_id=s.organization_id AND om.user_id=s.submitter_user_id
 AND om.status='active'
WHERE s.submitter_user_id IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM event_memberships em
    WHERE em.organization_id=s.organization_id AND em.event_id=s.event_id
      AND em.user_id=s.submitter_user_id AND em.role='speaker'
  )
GROUP BY s.organization_id,s.event_id,s.submitter_user_id;
