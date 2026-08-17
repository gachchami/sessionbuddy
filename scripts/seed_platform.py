"""Emit deterministic, non-production foundation seed rows as JSON."""

import argparse
import json
from uuid import NAMESPACE_URL, uuid5

BASE_MS = 1_786_154_400_000


def stable_id(seed: str, label: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"sessionbuddy:{seed}:{label}"))


def build_seed(seed: str = "foundation-v1") -> dict[str, list[dict[str, object]]]:
    organizations = []
    users = []
    organization_memberships = []
    events = []
    event_memberships = []
    authentication_challenges = []
    sessions = []
    roles = ("evaluator", "speaker")
    for org_number in range(1, 3):
        org_id = stable_id(seed, f"org:{org_number}")
        organizations.append(
            {
                "id": org_id,
                "name": f"Example Org {org_number}",
                "status": "active",
                "created_at_ms": BASE_MS,
                "updated_at_ms": BASE_MS,
            }
        )
        for role in roles:
            user_id = stable_id(seed, f"user:{org_number}:{role}")
            users.append(
                {
                    "id": user_id,
                    "email": f"{role}.{org_number}@example.test",
                    "normalized_email": f"{role}.{org_number}@example.test",
                    "status": "active",
                    "created_by_user_id": stable_id(seed, f"user:{org_number}:evaluator"),
                    "created_at_ms": BASE_MS,
                    "updated_at_ms": BASE_MS,
                }
            )
            organization_memberships.append(
                {
                    "id": stable_id(seed, f"org-member:{org_number}:{role}"),
                    "organization_id": org_id,
                    "user_id": user_id,
                    "role": "member",
                    "status": "active",
                    "created_at_ms": BASE_MS,
                    "updated_at_ms": BASE_MS,
                }
            )
        for event_number in range(1, 3):
            event_id = stable_id(seed, f"event:{org_number}:{event_number}")
            events.append(
                {
                    "id": event_id,
                    "organization_id": org_id,
                    "name": f"Summit {event_number}",
                    "starts_at_ms": BASE_MS,
                    "ends_at_ms": BASE_MS + 86_400_000,
                    "time_zone": "UTC",
                    "delivery_mode": "hybrid",
                    "status": "active",
                    "created_at_ms": BASE_MS,
                    "updated_at_ms": BASE_MS,
                }
            )
            for role in roles:
                user_id = stable_id(seed, f"user:{org_number}:{role}")
                event_memberships.append(
                    {
                        "id": stable_id(seed, f"event-member:{org_number}:{event_number}:{role}"),
                        "organization_id": org_id,
                        "event_id": event_id,
                        "user_id": user_id,
                        "role": role,
                        "status": "active",
                        "created_at_ms": BASE_MS,
                        "updated_at_ms": BASE_MS,
                    }
                )
        revoked_user_id = stable_id(seed, f"user:{org_number}:revoked")
        users.append(
            {
                "id": revoked_user_id,
                "email": f"revoked.{org_number}@example.test",
                "normalized_email": f"revoked.{org_number}@example.test",
                "status": "active",
                "created_at_ms": BASE_MS,
                "updated_at_ms": BASE_MS,
            }
        )
        organization_memberships.append(
            {
                "id": stable_id(seed, f"org-member:{org_number}:revoked"),
                "organization_id": org_id,
                "user_id": revoked_user_id,
                "role": "member",
                "status": "revoked",
                "created_at_ms": BASE_MS,
                "updated_at_ms": BASE_MS,
                "revoked_at_ms": BASE_MS + 1,
            }
        )
        sessions.extend(
            (
                {
                    "id": stable_id(seed, f"session:{org_number}:active"),
                    "user_id": stable_id(seed, f"user:{org_number}:evaluator"),
                    "token_hash": f"synthetic-active-{org_number}",
                    "csrf_secret_hash": "synthetic",
                    "authorization_version": 1,
                    "created_at_ms": BASE_MS,
                    "last_seen_at_ms": BASE_MS,
                    "idle_expires_at_ms": BASE_MS + 3_600_000,
                    "absolute_expires_at_ms": BASE_MS + 86_400_000,
                },
                {
                    "id": stable_id(seed, f"session:{org_number}:revoked"),
                    "user_id": stable_id(seed, f"user:{org_number}:speaker"),
                    "token_hash": f"synthetic-revoked-{org_number}",
                    "csrf_secret_hash": "synthetic",
                    "authorization_version": 1,
                    "created_at_ms": BASE_MS,
                    "last_seen_at_ms": BASE_MS,
                    "idle_expires_at_ms": BASE_MS + 3_600_000,
                    "absolute_expires_at_ms": BASE_MS + 86_400_000,
                    "revoked_at_ms": BASE_MS + 1,
                    "revoke_reason": "seed_fixture",
                },
            )
        )
        authentication_challenges.extend(
            (
                {
                    "id": stable_id(seed, f"challenge:{org_number}:expired"),
                    "normalized_email": f"speaker.{org_number}@example.test",
                    "token_hash": f"synthetic-expired-{org_number}",
                    "purpose": "sign_in",
                    "provisioning_context": "existing_user",
                    "redirect_path": "/",
                    "expires_at_ms": BASE_MS - 1,
                    "created_at_ms": BASE_MS - 901_000,
                },
                {
                    "id": stable_id(seed, f"challenge:{org_number}:consumed"),
                    "normalized_email": f"speaker.{org_number}@example.test",
                    "token_hash": f"synthetic-consumed-{org_number}",
                    "purpose": "sign_in",
                    "provisioning_context": "existing_user",
                    "redirect_path": "/",
                    "expires_at_ms": BASE_MS + 900_000,
                    "consumed_at_ms": BASE_MS,
                    "created_at_ms": BASE_MS - 1,
                },
            )
        )
    return {
        "organizations": organizations,
        "users": users,
        "organization_memberships": organization_memberships,
        "events": events,
        "event_memberships": event_memberships,
        "authentication_challenges": authentication_challenges,
        "sessions": sessions,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", default="foundation-v1")
    parser.add_argument("--environment", default="local", choices=("local", "test", "preview"))
    args = parser.parse_args()
    print(json.dumps(build_seed(args.seed), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
