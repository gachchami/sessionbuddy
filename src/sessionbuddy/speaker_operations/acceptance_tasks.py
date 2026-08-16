"""Shared acceptance-task eligibility used by decisions and speaker restoration.

Keep the SQL flags and task selection here rather than copying them into route
queries. The decision and correction paths previously carried near-identical
``EXISTS`` clauses; ``has_slides_task`` reached one query but not the other,
leaving the duplicate-task guard silently inert on corrections. Participation
restoration is a third consumer, so one definition is a correctness boundary,
not merely a convenience.
"""

from collections.abc import Awaitable, Callable

from sessionbuddy.platform.db.commands import CommandBatch
from sessionbuddy.platform.db.d1 import result_rows
from sessionbuddy.platform.db.types import new_id

SPEAKER_TASK_FLAGS_SQL = """
                      EXISTS(
                        SELECT 1 FROM speaker_tasks st
                         WHERE st.organization_id=s.organization_id
                           AND st.event_id=s.event_id
                           AND st.event_speaker_id=ss.event_speaker_id
                           AND st.task_type='profile' AND st.state='open'
                      ) AS has_profile_task,
                      EXISTS(
                        SELECT 1 FROM speaker_tasks st
                         WHERE st.organization_id=s.organization_id
                           AND st.event_id=s.event_id
                           AND st.event_speaker_id=ss.event_speaker_id
                           AND st.task_type='headshot' AND st.state='open'
                      ) AS has_headshot_task,
                      EXISTS(
                        SELECT 1 FROM speaker_tasks st
                         WHERE st.organization_id=s.organization_id
                           AND st.event_id=s.event_id
                           AND st.event_speaker_id=ss.event_speaker_id
                           AND st.submission_id=s.id
                           AND st.task_type='slides' AND st.state='open'
                      ) AS has_slides_task
"""


def acceptance_speaker_tasks(
    speaker: dict[str, object],
    *,
    include_slides: bool = True,
) -> list[tuple[str, str, str, int]]:
    """Return missing onboarding work for an accepted participation.

    Profile and headshot work belongs to every accepted participant. Slides
    remain scoped to the primary speaker for the accepted submission.
    """
    tasks: list[tuple[str, str, str, int]] = []
    if not str(speaker.get("biography") or "").strip() and not bool(
        speaker.get("has_profile_task")
    ):
        tasks.append(
            (
                "profile",
                "Add your speaker biography",
                "Your registration is complete; add the missing biography for the program.",
                7,
            )
        )
    if (
        not bool(speaker.get("has_account_headshot"))
        and not bool(speaker.get("has_event_headshot"))
        and not bool(speaker.get("has_headshot_task"))
    ):
        tasks.append(
            (
                "headshot",
                "Upload your headshot",
                "Add a program-ready profile photo.",
                10,
            )
        )
    if include_slides and not bool(speaker.get("has_slides_task")):
        tasks.append(
            (
                "slides",
                "Upload your presentation",
                "Share the final slide deck with the event team.",
                21,
            )
        )
    return tasks


def acceptance_requires_onboarding(
    speaker: dict[str, object], *, include_slides: bool = True
) -> bool:
    """Whether accepted work is missing or already open for this participation."""
    if acceptance_speaker_tasks(speaker, include_slides=include_slides):
        return True
    return bool(
        speaker.get("has_profile_task")
        or speaker.get("has_headshot_task")
        or (include_slides and speaker.get("has_slides_task"))
    )


def append_acceptance_speaker_tasks(
    batch,
    db,
    speaker: dict[str, object],
    *,
    organization_id: str,
    event_id: str,
    submission_id: str,
    event_speaker_id: str,
    now: int,
    include_slides: bool = True,
) -> int:
    """Append missing accepted-speaker tasks and return how many were requested."""
    tasks = acceptance_speaker_tasks(speaker, include_slides=include_slides)
    for task_type, title, help_text, days in tasks:
        batch.add_statement(
            db.prepare(
                """INSERT INTO speaker_tasks
                   (id,organization_id,event_id,event_speaker_id,submission_id,task_type,
                    title,help_text,destination_type,state,due_at_ms,created_at_ms,updated_at_ms)
                   SELECT ?1,?2,?3,?4,?5,?6,?7,?8,?6,'open',?9,?10,?10
                   WHERE NOT EXISTS (
                     SELECT 1 FROM speaker_tasks existing
                      WHERE existing.organization_id=?2 AND existing.event_id=?3
                        AND existing.event_speaker_id=?4
                        AND existing.task_type=?6 AND existing.state='open'
                        AND (?6!='slides' OR existing.submission_id=?5)
                   )"""
            ).bind(
                new_id(),
                organization_id,
                event_id,
                event_speaker_id,
                submission_id,
                task_type,
                title,
                help_text,
                now + days * 86_400_000,
                now,
            )
        )
    return len(tasks)


async def reconcile_accepted_submission_speakers(
    db,
    *,
    organization_id: str,
    event_id: str,
    submission_id: str,
    now: int,
    execute_batch: Callable[[CommandBatch], Awaitable[None]],
) -> None:
    """Converge accepted participants after invitation/decision races.

    Invitation acceptance and final-decision recording are independent writes.
    Whichever commits second calls this against committed state, so a participant
    cannot miss onboarding merely because each request's preflight read happened
    before the other request committed.
    """
    effective = await db.prepare(
        """SELECT COALESCE((SELECT correction.corrected_decision
                     FROM submission_decision_corrections correction
                    WHERE correction.organization_id=s.organization_id
                      AND correction.event_id=s.event_id
                      AND correction.submission_id=s.id
                    ORDER BY correction.corrected_at_ms DESC,correction.id DESC LIMIT 1),
                   decision.decision) AS effective_decision
             FROM submissions s
             LEFT JOIN submission_decisions decision
               ON decision.organization_id=s.organization_id
              AND decision.event_id=s.event_id AND decision.submission_id=s.id
            WHERE s.id=?1 AND s.organization_id=?2 AND s.event_id=?3 LIMIT 1"""
    ).bind(submission_id, organization_id, event_id).first("effective_decision")
    if str(effective or "") != "accepted":
        return

    query = SPEAKER_TASK_FLAGS_SQL.join(
        (
            """SELECT ss.event_speaker_id,ss.role,es.status AS event_speaker_status,
                      COALESCE(NULLIF(p.biography,''),u.description,'') AS biography,
                      EXISTS(SELECT 1 FROM user_headshots uh WHERE uh.user_id=p.user_id)
                        AS has_account_headshot,
                      EXISTS(SELECT 1 FROM speaker_assets sa
                        JOIN speaker_asset_versions av ON av.asset_id=sa.id
                          AND av.is_current=1 AND av.scan_state='clean'
                        WHERE sa.organization_id=s.organization_id AND sa.event_id=s.event_id
                          AND sa.event_speaker_id=ss.event_speaker_id AND sa.kind='headshot')
                        AS has_event_headshot,
""",
            """
               FROM submissions s
               JOIN submission_speakers ss ON ss.organization_id=s.organization_id
                 AND ss.event_id=s.event_id AND ss.submission_id=s.id
               JOIN event_speakers es ON es.organization_id=s.organization_id
                 AND es.event_id=s.event_id AND es.id=ss.event_speaker_id
               JOIN people p ON p.organization_id=s.organization_id AND p.id=es.person_id
               JOIN users u ON u.id=p.user_id
               WHERE s.id=?1 AND s.organization_id=?2 AND s.event_id=?3""",
        )
    )
    speakers = result_rows(
        await db.prepare(query).bind(submission_id, organization_id, event_id).all()
    )
    batch = CommandBatch(db)
    for speaker in speakers:
        if str(speaker["event_speaker_status"]) == "withdrawn":
            continue
        include_slides = str(speaker["role"]) == "primary"
        onboarding = acceptance_requires_onboarding(
            speaker, include_slides=include_slides
        )
        event_speaker_id = str(speaker["event_speaker_id"])
        append_acceptance_speaker_tasks(
            batch,
            db,
            speaker,
            organization_id=organization_id,
            event_id=event_id,
            submission_id=submission_id,
            event_speaker_id=event_speaker_id,
            now=now,
            include_slides=include_slides,
        )
        batch.add_statement(
            db.prepare(
                """UPDATE event_speakers SET selection_status='accepted',status=?1,
                         accepted_at_ms=COALESCE(accepted_at_ms,?2),
                         last_activity_at_ms=?2,updated_at_ms=?2
                   WHERE organization_id=?3 AND event_id=?4 AND id=?5
                     AND status!='withdrawn'"""
            ).bind(
                "onboarding" if onboarding else "complete",
                now,
                organization_id,
                event_id,
                event_speaker_id,
            )
        )
    if batch.statement_count:
        await execute_batch(batch)
