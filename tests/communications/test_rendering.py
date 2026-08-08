import pytest

from sessionbuddy.communications.rendering import render_template, validate_template


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


def test_missing_value_is_rejected_instead_of_silently_blank() -> None:
    with pytest.raises(ValueError):
        render_template("Hello {{speaker.name}}", {})
