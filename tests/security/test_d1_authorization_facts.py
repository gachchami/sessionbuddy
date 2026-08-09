from sessionbuddy.platform.auth.d1 import D1AuthorizationFacts
from sessionbuddy.platform.authorization.types import Role


class Statement:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.parameters: tuple[object, ...] = ()

    def bind(self, *values: object) -> "Statement":
        self.parameters = values
        return self

    async def all(self) -> dict[str, object]:
        return {"results": self.rows}


class CountingDatabase:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.queries: list[str] = []

    def prepare(self, query: str) -> Statement:
        self.queries.append(query)
        return Statement(self.rows)


async def test_actor_facts_use_one_database_round_trip_and_ignore_affiliation_role() -> None:
    database = CountingDatabase(
        [
            {
                "fact_type": "principal",
                "user_id": "user-a",
                "user_status": "active",
                "organization_id": None,
                "event_id": None,
                "role": None,
            },
            {
                "fact_type": "organization",
                "user_id": "user-a",
                "user_status": "active",
                "organization_id": "org-a",
                "event_id": None,
                "role": "member",
            },
            {
                "fact_type": "event",
                "user_id": "user-a",
                "user_status": "active",
                "organization_id": "org-a",
                "event_id": "event-a",
                "role": "speaker",
            },
        ]
    )

    actor = await D1AuthorizationFacts(database).actor_for_session("session-a")

    assert actor is not None
    assert actor.organization_roles == {}
    assert actor.event_roles == {("org-a", "event-a"): frozenset({Role.SPEAKER})}
    assert len(database.queries) == 1
