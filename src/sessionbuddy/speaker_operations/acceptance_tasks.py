"""Shared acceptance-task eligibility used by decisions and speaker restoration.

Keep the SQL flags and task selection here rather than copying them into route
queries. The decision and correction paths previously carried near-identical
``EXISTS`` clauses; ``has_slides_task`` reached one query but not the other,
leaving the duplicate-task guard silently inert on corrections. Participation
restoration is a third consumer, so one definition is a correctness boundary,
not merely a convenience.
"""

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
