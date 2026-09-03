"""Source and wiring checks for the single-organization settings contract.

`/admin/organization` edits exactly one organization, chosen by
`?organization_id=` with a session fallback, and drops slow responses that
belong to a previously selected organization. These tests pin the source
literals that carry that contract; the rendered behavior is covered by
harness/e2e/account-responsive.spec.ts.
"""

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
STATIC = ROOT / "src" / "sessionbuddy" / "static"


def read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_source_wiring_organization_settings_select_one_organization_from_the_url() -> None:
    script = read("organization_admin.js")

    assert '.get("organization_id")' in script
    assert 'history.replaceState(null, ""' in script
    # Unknown or unauthorized ids fall back to the session organization and
    # say so, rather than silently editing a different organization.
    assert "The requested organization is not available to you." in script
    session_fallback = (
        "organizations.some((organization) => organization.id === session.organization_id)"
    )
    assert session_fallback in script
    assert 'byId("organization-switcher").addEventListener("change"' in script


def test_source_wiring_stale_organization_responses_never_render() -> None:
    script = read("organization_admin.js")

    assert "state.loadToken" in script
    assert "token !== state.loadToken" in script
    # Every loader that awaits the network re-checks the token afterwards.
    for loader in ("loadGrants", "loadInvitations", "loadActivity"):
        assert f"async function {loader}(organizationId, token = state.loadToken)" in script
    # A late response for a panel that has been replaced drops instead of throwing.
    section_guard = "const section = accessSection(organizationId);\n    if (!section) return;"
    assert script.count(section_guard) == 2
    # Activity is requested only for the selected organization, never fanned out.
    assert "Promise.allSettled([loadGrants(id, token), loadActivity(id, token)])" in script
    assert "organizations.flatMap(" not in script


def test_source_wiring_activity_filters_are_client_side_over_the_api_page() -> None:
    script = read("organization_admin.js")
    page = read("organization_admin.html")

    assert 'id="organization-activity-filters"' in page
    assert 'id="organization-activity-summary"' in page
    assert 'data-activity-filter="all"' in page
    for kind in ("events", "cfp", "proposals", "speakers", "agenda", "invitations", "access"):
        assert f'data-activity-filter="{kind}"' in page
    assert "Showing the 30 most recent changes." in script
    assert "Showing ${rows.length} of the ${total} most recent changes." in script
    assert "No changes recorded for ${name} yet." in script
    # No paging parameters are added to the activities request.
    assert re.search(r"/activities`\)", script)
    assert "/activities?" not in script


def test_source_wiring_organization_settings_markup_declares_switcher_and_status_order() -> None:
    page = read("organization_admin.html")

    assert 'id="organization-switcher"' in page
    assert 'id="organization-switcher-label"' in page
    assert 'id="organization-settings-title"' in page
    assert 'id="organization-activity-summary"' in page
    assert "<optgroup" not in page
    assert "account-profile-panel" not in page
    assert "Roles and Access" not in page
    assert "organization_admin" not in page
    assert 'organization.js?v=3' in page
    # `#status` must be the first role="status" so status.first() reads it.
    status_positions = [match.start() for match in re.finditer(r'role="status"', page)]
    assert status_positions
    assert page.index('id="status"') < status_positions[0]
    assert page.index('id="status"') < page.index('id="organization-activity-summary"')


def test_source_wiring_home_links_carry_the_selected_organization() -> None:
    home = read("admin_home.js")

    assert (
        'byId("organization-settings").href = '
        "`/admin/organization?organization_id=${encodeURIComponent(state.organizationId)}`"
    ) in home
    assert (
        "/admin/organization?organization_id=${encodeURIComponent(state.organizationId)}#organization-activity"
    ) in home


def test_source_wiring_activity_format_labels_organizer_access_grants() -> None:
    activity_format = read("activity_format.js")

    assert "resource_access_grant" in activity_format


def test_source_wiring_rename_and_switch_keep_one_organization_consistent() -> None:
    script = read("organization_admin.js")

    # A rename response that lands after a switch may not relabel the page.
    assert "if (form.dataset.organizationId === state.organizationId) {" in script
    # The rebuilt name form must carry the saved version, or the next save is a 409.
    assert "entry.version = organization.version;" in script
    # An unknown switcher value is rejected before state or the URL changes.
    assert 'userError("That organization is not available to you.")' in script
    assert script.index('userError("That organization is not available to you.")') < script.index(
        "state.organizationId = id;\n    syncUrl();"
    )
    # Focus and the announcement move with the change, before the network settles.
    assert script.index("Now showing ${organization.name}.") < script.index(
        "const token = ++state.loadToken;"
    )


def test_source_wiring_failed_loads_replace_every_loading_line() -> None:
    script = read("organization_admin.js")

    for loading, failed in (
        ("Loading activity…", "Activity could not be loaded."),
        ("Loading your organizations…", "Your organizations could not be loaded."),
        ("Loading…", "Not loaded."),
    ):
        assert f'"{loading}", "{failed}"' in script
