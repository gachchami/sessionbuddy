"""Create deterministic, synthetic production-envelope data in SQLite/D1 schema.

The generator writes in bounded batches and deliberately contains no real-looking
personal data.  It is suitable for local and isolated preview databases only.
"""

from __future__ import annotations

import argparse
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

BASE_MS = 1_786_154_400_000
BATCH_SIZE = 1_000


@dataclass(frozen=True, slots=True)
class SeedScale:
    submissions: int = 10_000
    speakers: int = 2_000
    tasks: int = 50_000
    agenda_items: int = 2_000

    def validate(self) -> None:
        if min(self.submissions, self.speakers, self.tasks, self.agenda_items) < 0:
            raise ValueError("seed counts cannot be negative")
        if self.tasks and not self.speakers:
            raise ValueError("tasks require at least one speaker")
        if self.tasks and not self.submissions:
            raise ValueError("tasks require at least one submission")
        if self.agenda_items > min(self.submissions, self.speakers):
            raise ValueError("agenda items require one submission and speaker each")


def batched[T](rows: Iterable[T], size: int = BATCH_SIZE) -> Iterator[list[T]]:
    batch: list[T] = []
    for row in rows:
        batch.append(row)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def insert_many(db: sqlite3.Connection, sql: str, rows: Iterable[Sequence[object]]) -> None:
    for batch in batched(rows):
        db.executemany(sql, batch)


def apply_migrations(db: sqlite3.Connection, migrations: Path) -> None:
    migration_files = sorted(migrations.glob("*.sql"))
    if not migration_files:
        raise ValueError(f"no migrations found in {migrations}")
    for migration in migration_files:
        db.executescript(migration.read_text(encoding="utf-8"))


def seed_large(db: sqlite3.Connection, scale: SeedScale | None = None) -> None:
    """Populate an empty migrated database with one deterministic large event."""
    scale = scale or SeedScale()
    scale.validate()
    db.execute("PRAGMA foreign_keys = ON")
    event_end = BASE_MS + 7 * 86_400_000

    with db:
        db.execute(
            "INSERT INTO organizations(id,name,status,created_at_ms,updated_at_ms) "
            "VALUES('load-org','Synthetic Load Org','active',?,?)",
            (BASE_MS, BASE_MS),
        )
        db.execute(
            """INSERT INTO users
               (id,email,normalized_email,status,email_verified_at_ms,created_at_ms,updated_at_ms)
               VALUES('load-admin','admin@synthetic.invalid','admin@synthetic.invalid',
                      'active',?,?,?)""",
            (BASE_MS, BASE_MS, BASE_MS),
        )
        db.execute(
            """INSERT INTO organization_memberships
               (id,organization_id,user_id,role,status,created_at_ms,updated_at_ms)
               VALUES('load-org-admin','load-org','load-admin','organization_admin','active',?,?)""",
            (BASE_MS, BASE_MS),
        )
        db.execute(
            """INSERT INTO events
               (id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,delivery_mode,
                status,created_at_ms,updated_at_ms)
               VALUES('load-event','load-org','Synthetic Scale Event',?,?,'UTC','hybrid',
                      'active',?,?)""",
            (BASE_MS, event_end, BASE_MS, BASE_MS),
        )
        db.execute(
            """INSERT INTO call_for_speaker_forms
               (id,organization_id,event_id,version,slug,welcome_text,schema_json,
                status,published_at_ms,created_at_ms,updated_at_ms)
               VALUES('load-form','load-org','load-event',1,'synthetic-load-cfp',
                      'Synthetic load fixture','{}','published',?,?,?)""",
            (BASE_MS, BASE_MS, BASE_MS),
        )
        db.execute(
            """INSERT INTO evaluation_rounds
               (id,organization_id,event_id,name,rubric_json,status,
                created_at_ms,updated_at_ms)
               VALUES('load-round','load-org','load-event','Load Round','{}',
                      'open',?,?)""",
            (BASE_MS, BASE_MS),
        )
        db.execute(
            """INSERT INTO schedule_revisions
               (id,organization_id,event_id,revision_number,name,status,created_by_user_id,
                created_at_ms,updated_at_ms)
               VALUES('load-revision','load-org','load-event',1,'Synthetic Draft','draft',
                      'load-admin',?,?)""",
            (BASE_MS, BASE_MS),
        )

        insert_many(
            db,
            """INSERT INTO submissions
               (id,organization_id,event_id,form_id,public_session_id,
                proposal_title,proposal_abstract,speaker_name,status,submitted_at_ms,
                created_at_ms,updated_at_ms) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                (
                    f"load-submission-{number:05d}", "load-org", "load-event", "load-form",
                    f"load-public-{number:05d}", f"Synthetic proposal {number}",
                    "Synthetic performance fixture; contains no production data.",
                    f"Synthetic Speaker {number % max(scale.speakers, 1):04d}", "submitted",
                    BASE_MS + number, BASE_MS + number, BASE_MS + number,
                )
                for number in range(scale.submissions)
            ),
        )
        insert_many(
            db,
            """INSERT INTO people
               (id,organization_id,display_name,links_json,created_at_ms,updated_at_ms)
               VALUES(?, 'load-org', ?, '[]', ?, ?)""",
            ((f"load-person-{n:04d}", f"Synthetic Speaker {n:04d}", BASE_MS, BASE_MS)
             for n in range(scale.speakers)),
        )
        insert_many(
            db,
            """INSERT INTO event_speakers
               (id,organization_id,event_id,person_id,status,accepted_at_ms,last_activity_at_ms,
                created_at_ms,updated_at_ms)
               VALUES(?,'load-org','load-event',?,'onboarding',?,?,?,?)""",
            ((f"load-speaker-{n:04d}", f"load-person-{n:04d}", BASE_MS, BASE_MS + n,
              BASE_MS, BASE_MS) for n in range(scale.speakers)),
        )
        linked = min(scale.submissions, scale.speakers)
        insert_many(
            db,
            """INSERT INTO submission_speakers
               (id,organization_id,event_id,submission_id,event_speaker_id,role,snapshot_name,
                created_at_ms) VALUES(?,'load-org','load-event',?,?,'primary',?,?)""",
            ((f"load-submission-speaker-{n:04d}", f"load-submission-{n:05d}",
              f"load-speaker-{n:04d}", f"Synthetic Speaker {n:04d}", BASE_MS)
             for n in range(linked)),
        )
        insert_many(
            db,
            """INSERT INTO speaker_tasks
               (id,organization_id,event_id,event_speaker_id,submission_id,task_type,title,
                destination_type,state,due_at_ms,completed_at_ms,created_at_ms,updated_at_ms)
               VALUES(?,'load-org','load-event',?,?,?,?,?,?,?,?,?,?)""",
            (
                (
                    f"load-task-{n:05d}", f"load-speaker-{n % scale.speakers:04d}",
                    f"load-submission-{n % linked:05d}" if linked else None,
                    "slides" if n % 2 == 0 else "profile", f"Synthetic task {n}",
                    "slides" if n % 2 == 0 else "profile",
                    "completed" if n % 5 == 0 else "open", BASE_MS + (n % 30) * 86_400_000,
                    BASE_MS + n if n % 5 == 0 else None, BASE_MS, BASE_MS,
                )
                for n in range(scale.tasks)
            ),
        )

        room_count = min(100, max(scale.agenda_items, 1))
        insert_many(
            db,
            """INSERT INTO event_rooms
               (id,organization_id,event_id,name,status,created_at_ms,updated_at_ms)
               VALUES(?,'load-org','load-event',?,'active',?,?)""",
            ((f"load-room-{n:03d}", f"Room {n:03d}", BASE_MS, BASE_MS)
             for n in range(room_count)),
        )
        insert_many(
            db,
            """INSERT INTO submission_decisions
               (id,organization_id,event_id,round_id,submission_id,decision,internal_reason,
                decided_by_user_id,decided_at_ms,updated_at_ms)
               VALUES(?,'load-org','load-event','load-round',?,'accepted','Synthetic fixture',
                      'load-admin',?,?)""",
            ((f"load-decision-{n:04d}", f"load-submission-{n:05d}", BASE_MS, BASE_MS)
             for n in range(scale.agenda_items)),
        )
        insert_many(
            db,
            """INSERT INTO accepted_sessions
               (id,organization_id,event_id,submission_id,decision_id,created_at_ms)
               VALUES(?,'load-org','load-event',?,?,?)""",
            ((f"load-session-{n:04d}", f"load-submission-{n:05d}",
              f"load-decision-{n:04d}", BASE_MS) for n in range(scale.agenda_items)),
        )
        insert_many(
            db,
            """INSERT INTO agenda_items
               (id,organization_id,event_id,revision_id,accepted_session_id,room_id,event_date,
                event_time_zone,starts_at_ms,ends_at_ms,created_at_ms,updated_at_ms)
               VALUES(?,'load-org','load-event','load-revision',?,?, '2026-08-08','UTC',?,?,?,?)""",
            (
                (
                    f"load-agenda-{n:04d}", f"load-session-{n:04d}",
                    f"load-room-{n % room_count:03d}", BASE_MS + (n // room_count) * 1_800_000,
                    BASE_MS + (n // room_count + 1) * 1_800_000, BASE_MS, BASE_MS,
                )
                for n in range(scale.agenda_items)
            ),
        )
        insert_many(
            db,
            """INSERT INTO agenda_item_speakers
               (id,organization_id,event_id,revision_id,agenda_item_id,event_speaker_id,created_at_ms)
               VALUES(?,'load-org','load-event','load-revision',?,?,?)""",
            ((f"load-agenda-speaker-{n:04d}", f"load-agenda-{n:04d}",
              f"load-speaker-{n:04d}", BASE_MS) for n in range(scale.agenda_items)),
        )

    violations = db.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"foreign-key violations after seed: {violations[:5]}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--migrations", type=Path, default=Path("migrations_baseline"))
    parser.add_argument("--submissions", type=int, default=10_000)
    parser.add_argument("--speakers", type=int, default=2_000)
    parser.add_argument("--tasks", type=int, default=50_000)
    parser.add_argument("--agenda-items", type=int, default=2_000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.database.exists():
        raise SystemExit(f"refusing to overwrite existing database: {args.database}")
    args.database.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(args.database)
    try:
        apply_migrations(db, args.migrations)
        seed_large(
            db,
            SeedScale(args.submissions, args.speakers, args.tasks, args.agenda_items),
        )
    finally:
        db.close()
    print(f"created deterministic synthetic database: {args.database}")


if __name__ == "__main__":
    main()
