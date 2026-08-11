from pathlib import Path


STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def test_agenda_toolbar_groups_view_and_actions_for_responsive_layout() -> None:
    page = (STATIC / "agenda_admin.html").read_text()

    assert 'class="agenda-toolbar__view"' in page
    assert 'class="agenda-toolbar__actions"' in page
    assert "Schedule view" in page
    assert ">Public schedule<" in page


def test_agenda_mobile_toolbar_keeps_publish_as_the_primary_full_width_action() -> None:
    styles = (STATIC / "agenda.css").read_text()
    mobile = styles.split("@media (max-width:48rem)", 1)[1]

    assert ".agenda-toolbar__actions" in mobile
    assert "grid-template-columns: 1fr 1fr" in mobile
    assert ".agenda-toolbar__actions #publish" in mobile
    assert "grid-column: 1 / -1" in mobile
    assert "grid-template-columns: repeat(5, minmax(max-content,1fr))" in mobile
