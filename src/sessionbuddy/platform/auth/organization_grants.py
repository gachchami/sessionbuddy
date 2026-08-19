"""The single exact-resource writer for organization administrator access."""

from sessionbuddy.platform.db.types import new_id


def append_organization_manage_grant(
    batch, db, *, organization_id: str, user_id: str, granted_by_user_id: str, now: int
) -> None:
    batch.add_statement(
        db.prepare(
            """INSERT INTO organization_memberships
           (id,organization_id,user_id,role,status,created_at_ms,updated_at_ms)
           VALUES(?1,?2,?3,'member','active',?4,?4)
           ON CONFLICT(organization_id,user_id) DO UPDATE SET status='active',
             revoked_at_ms=NULL,
             role=CASE WHEN organization_memberships.status='revoked' THEN 'member'
                       ELSE organization_memberships.role END,
             version=version+1,updated_at_ms=excluded.updated_at_ms"""
        ).bind(new_id(), organization_id, user_id, now)
    )
    batch.add_statement(
        db.prepare(
            """UPDATE resource_access_grants SET status='revoked',revoked_at_ms=?1,
             revoked_by_user_id=?2,version=version+1,updated_at_ms=?1
           WHERE resource_id=?3 AND user_id=?4 AND status='active' AND permission!='manage'"""
        ).bind(now, granted_by_user_id, organization_id, user_id)
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO resource_access_grants
           (id,resource_id,user_id,permission,status,granted_by_user_id,created_at_ms,updated_at_ms)
           VALUES(?1,?2,?3,'manage','active',?4,?5,?5)
           ON CONFLICT(resource_id,user_id,permission) DO UPDATE SET status='active',
             granted_by_user_id=excluded.granted_by_user_id,revoked_at_ms=NULL,
             revoked_by_user_id=NULL,version=version+1,updated_at_ms=excluded.updated_at_ms"""
        ).bind(new_id(), organization_id, user_id, granted_by_user_id, now)
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO user_roles(user_id,role,status,created_at_ms,updated_at_ms,is_default)
           VALUES(?1,'organizer','active',?2,?2,
             CASE WHEN EXISTS(SELECT 1 FROM user_roles WHERE user_id=?1 AND status='active')
                  THEN 0 ELSE 1 END)
           ON CONFLICT(user_id,role) DO UPDATE SET status='active',revoked_at_ms=NULL,
             is_default=CASE WHEN NOT EXISTS(SELECT 1 FROM user_roles other
               WHERE other.user_id=?1 AND other.status='active' AND other.role!='organizer')
               THEN 1 ELSE user_roles.is_default END,updated_at_ms=excluded.updated_at_ms"""
        ).bind(user_id, now)
    )
    batch.add_statement(
        db.prepare(
            "UPDATE users SET authorization_version=authorization_version+1,"
            "updated_at_ms=?1 WHERE id=?2"
        ).bind(now, user_id)
    )
