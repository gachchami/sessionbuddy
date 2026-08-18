import pytest

from sessionbuddy.communications.rendering import (
    DECISION_MESSAGE_VARIABLES,
    SPEAKER_MESSAGE_VARIABLES,
    TemplateVariableError,
    render_template,
    validate_template,
)


def test_variables_are_html_escaped_and_not_executable() -> None:
    rendered = render_template(
        "<h1>Hello {{ speaker.name }}</h1><p>{{submission.title}}</p>",
        {"speaker.name": '<img src=x onerror="alert(1)">', "submission.title": "A & B"},
    )
    assert "<img" not in rendered
    assert "onerror=&quot;alert(1)&quot;" in rendered
    assert "A &amp; B" in rendered


@pytest.mark.parametrize(
    "template", ["{{ secret.value }}", "{{speaker.name", "speaker.name}}", "{{ Speaker.name }}"]
)
def test_unknown_or_malformed_variables_are_rejected(template: str) -> None:
    with pytest.raises(ValueError):
        validate_template(template)


def test_unknown_variable_error_names_the_token_and_catalog() -> None:
    with pytest.raises(ValueError) as error:
        validate_template("Open {{event.portal_link}} after {{speaker.name}} signs in")

    message = str(error.value)
    assert "event.portal_link" in message
    assert "portal.link" in message
    assert "speaker.name" in message


def test_speaker_message_error_lists_only_fields_available_in_the_composer() -> None:
    with pytest.raises(ValueError) as error:
        validate_template(
            "Open {{event.portal_link}} after {{speaker.name}} signs in",
            allowed_variables=SPEAKER_MESSAGE_VARIABLES,
        )

    message = str(error.value)
    assert "event.portal_link" in message
    assert "portal.link" in message
    assert "speaker.name" in message
    assert "schedule.room" not in message


def test_known_but_context_inapplicable_variable_has_a_distinct_error_code() -> None:
    with pytest.raises(TemplateVariableError) as error:
        validate_template(
            "Meet in {{schedule.room}}",
            allowed_variables=SPEAKER_MESSAGE_VARIABLES,
        )

    assert error.value.code == "template_variable_unavailable"
    assert error.value.variables == ("schedule.room",)


def test_alias_suggestions_are_structured_and_context_aware() -> None:
    with pytest.raises(TemplateVariableError) as speaker_error:
        validate_template(
            "Open {{portal_link}}",
            allowed_variables=SPEAKER_MESSAGE_VARIABLES,
        )
    assert speaker_error.value.suggestions == {"portal_link": "portal.link"}

    with pytest.raises(TemplateVariableError) as namespaced_error:
        validate_template(
            "Open {{event.portal_link}}",
            allowed_variables=SPEAKER_MESSAGE_VARIABLES,
        )
    assert namespaced_error.value.suggestions == {"event.portal_link": "portal.link"}

    with pytest.raises(TemplateVariableError) as decision_error:
        validate_template(
            "Open {{portal_link}}",
            allowed_variables=DECISION_MESSAGE_VARIABLES,
        )
    assert decision_error.value.suggestions == {}


def test_missing_value_is_rejected_instead_of_silently_blank() -> None:
    with pytest.raises(ValueError):
        render_template("Hello {{speaker.name}}", {})
