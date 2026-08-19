"""Proposal receipts retain organizer copy and identify the associated event."""

from tests.cfp.test_staged_uploads import (
    _client,
    _sign_in,
    _stage_file,
    _submit,
    cfp_environment,  # noqa: F401
)


async def test_submission_confirmation_contains_escaped_event_and_proposal(cfp_environment):  # noqa: F811
    connection, environment = cfp_environment
    connection.execute("UPDATE events SET name='DevFlow <2027> & friends' WHERE id='event'")
    connection.execute(
        "UPDATE call_for_speaker_forms SET confirmation_subject='Custom receipt',"
        "confirmation_body='Custom organizer thanks' WHERE id='form'"
    )
    connection.commit()
    async with _client(environment) as client:
        csrf = await _sign_in(client, connection, "speaker@example.test")
        staged_id = await _stage_file(client, csrf)
        response = await _submit(client, csrf, staged_id, title="CI <builds>")
        assert response.status_code == 201, response.text
    row = connection.execute(
        "SELECT subject,html_body FROM communication_messages "
        "WHERE deterministic_key LIKE 'submission-confirmation:%'"
    ).fetchone()
    assert row[0] == "Custom receipt"
    assert "Custom organizer thanks" in row[1]
    assert "DevFlow &lt;2027&gt; &amp; friends" in row[1]
    assert "CI &lt;builds&gt;" in row[1]
