import json
from pathlib import Path

from scripts.audit_rule_chokepoints import (
    ROOT,
    advisory_literal_clusters,
    canonical_constant_findings,
    declaration_findings,
    derived_rule_inventory_findings,
    mechanical_findings,
    persisted_enum_findings,
    session_mock_findings,
    source_test_name_findings,
    writer_inventory_findings,
)


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_rule_audit_has_no_mechanical_findings_in_the_current_tree() -> None:
    config = json.loads((ROOT / "rule_audit.json").read_text(encoding="utf-8"))
    assert mechanical_findings(ROOT, config) == []


def test_source_test_name_detector_is_calibrated_to_the_escaped_brand_shape(
    tmp_path: Path,
) -> None:
    write(
        tmp_path / "tests/test_brand.py",
        """from pathlib import Path

def test_account_brand_uses_the_active_role_destination():
    javascript = Path('app_shell.js').read_text()
    assert 'activeDestination' in javascript

def test_account_brand_wiring_uses_the_active_role_destination():
    javascript = Path('app_shell.js').read_text()
    assert 'activeDestination' in javascript
""",
    )

    findings = source_test_name_findings(tmp_path)

    assert [finding.code for finding in findings] == ["source_test_behavioral_name"]
    assert "test_account_brand_uses_the_active_role_destination" in findings[0].message


def test_writer_inventory_detector_is_calibrated_to_six_registered_task_writers(
    tmp_path: Path,
) -> None:
    writers = "\n".join(  # noqa: S608 - source fixture, never executed as SQL
        f"""def writer_{number}(db):
    db.prepare(\"INSERT INTO speaker_tasks (id) VALUES (?)\")
"""  # noqa: S608 - source fixture, never executed as SQL
        for number in range(6)
    )
    path = tmp_path / "src/sessionbuddy/tasks.py"
    write(path, writers)
    registered = {
        "writer_inventories": {
            "speaker_tasks": [
                f"src/sessionbuddy/tasks.py:writer_{number}" for number in range(6)
            ]
        }
    }
    assert writer_inventory_findings(tmp_path, registered) == []

    write(
        path,
        writers  # noqa: S608 - source fixture, never executed as SQL
        + """
def writer_six(db):
    db.prepare("INSERT INTO speaker_tasks (id) VALUES (?)")
""",
    )
    findings = writer_inventory_findings(tmp_path, registered)
    assert [finding.code for finding in findings] == ["unregistered_writer"]
    assert "writer_six" in findings[0].message


def test_derived_rule_inventory_is_calibrated_to_manages_organization(
    tmp_path: Path,
) -> None:
    path = tmp_path / "src/sessionbuddy/access.py"
    registered_source = '''def list_events(actor):
    manages_organization = actor.is_owner or actor.can_manage
    return manages_organization

def current_session(access):
    manages_organization = bool(access)
    return manages_organization

def user_workspace_contract(db):
    query = "SELECT EXISTS (SELECT 1) AS manages_organization"
    return query
'''
    write(path, registered_source)
    config = {
        "derived_rule_inventories": [
            {
                "id": "manages_organization",
                "symbol": "manages_organization",
                "user_path": "Organizer navigation and route access can contradict each other.",
                "sites": [
                    "src/sessionbuddy/access.py:list_events",
                    "src/sessionbuddy/access.py:current_session",
                    "src/sessionbuddy/access.py:user_workspace_contract",
                ],
            }
        ]
    }
    assert derived_rule_inventory_findings(tmp_path, config) == []

    write(
        path,
        registered_source
        + '''
def fourth_reconstruction(actor):
    return resolve_workspace(manages_organization=bool(actor.authority))
''',
    )

    findings = derived_rule_inventory_findings(tmp_path, config)

    assert [finding.code for finding in findings] == [
        "unregistered_derived_rule_site"
    ]
    assert "fourth_reconstruction" in findings[0].message


def test_session_mock_detector_reads_allowed_fields_from_openapi(tmp_path: Path) -> None:
    write(
        tmp_path / "openapi/openapi.json",
        json.dumps(
            {
                "components": {
                    "schemas": {
                        "SessionEventAccess": {
                            "properties": {
                                "organization_id": {},
                                "event_id": {},
                                "event_name": {},
                                "assignments": {},
                            }
                        }
                    }
                }
            }
        ),
    )
    write(
        tmp_path / "harness/e2e/persona-resource-boundary.spec.ts",
        """const session = {
  event_access: [{
    organization_id: "org",
    event_id: "event",
    event_name: "Event",
    permissions: ["manage"],
    assignments: ["speaker"],
  }],
};
""",
    )

    findings = session_mock_findings(tmp_path)

    assert [finding.code for finding in findings] == [
        "session_mock_unknown_event_access_field"
    ]
    assert findings[0].message.endswith("permissions")


def test_canonical_constant_detector_is_calibrated_to_duplicate_policy_tables(
    tmp_path: Path,
) -> None:
    write(
        tmp_path / "src/sessionbuddy/platform/uploads.py",
        "ASSET_UPLOAD_RULES = {'headshot': ('image/png', 5), 'slides': ('pdf', 50)}\n",
    )
    write(
        tmp_path / "src/sessionbuddy/feature.py",
        "ASSET_RULES = {'headshot': ('image/png', 5), 'slides': ('pdf', 50)}\n",
    )
    config = {
        "canonical_constants": [
            {
                "id": "uploads",
                "path": "src/sessionbuddy/platform/uploads.py",
                "symbol": "ASSET_UPLOAD_RULES",
                "minimum_shared_entries": 2,
                "allowed_projections": [],
            }
        ]
    }

    findings = canonical_constant_findings(tmp_path, config)

    assert [finding.code for finding in findings] == ["duplicate_canonical_constant"]
    assert "2 entries" in findings[0].message


def test_persisted_enum_detector_is_calibrated_to_the_inert_view_tier(
    tmp_path: Path,
) -> None:
    write(
        tmp_path / "src/sessionbuddy/types.py",
        """from enum import StrEnum

class ResourceGrant(StrEnum):
    VIEW = "view"
    MANAGE = "manage"
""",
    )
    write(
        tmp_path / "src/sessionbuddy/policy.py",
        """from sessionbuddy.types import ResourceGrant

def permits(grants):
    return ResourceGrant.MANAGE in grants
""",
    )
    config = {
        "persisted_enums": [
            {
                "id": "resource_grant",
                "path": "src/sessionbuddy/types.py",
                "symbol": "ResourceGrant",
            }
        ]
    }

    findings = persisted_enum_findings(tmp_path, config)

    assert [finding.code for finding in findings] == ["inert_persisted_enum_member"]
    assert "ResourceGrant.VIEW" in findings[0].message


def test_exception_declarations_fail_when_their_removal_trigger_has_fired(
    tmp_path: Path,
) -> None:
    write(tmp_path / "migrations_baseline/0001.sql", "SELECT 1;\n")
    config = {
        "temporary_duplications": [
            {
                "id": "legacy-sentinel",
                "path": "src/legacy.py",
                "owner": "platform",
                "removal": "remove the sentinel after the baseline rebase",
                "user_path": "Legacy values remain visible through the API.",
                "trigger": {
                    "kind": "migration_count_at_least",
                    "path": "migrations_baseline",
                    "count": 1,
                },
            }
        ],
        "waivers": [],
    }

    findings = declaration_findings(tmp_path, config)

    assert [finding.code for finding in findings] == [
        "temporary_duplication_triggered"
    ]
    assert findings[0].severity == "P1"


def test_literal_vocabulary_clusters_are_advisory_discovery_only(tmp_path: Path) -> None:
    write(
        tmp_path / "src/sessionbuddy/server.py",
        """def destination(role):
    return {'organizer': '/admin', 'reviewer': '/reviews', 'speaker': '/speaker'}.get(role)
""",
    )
    write(
        tmp_path / "src/sessionbuddy/client.py",
        """def other_destination(role):
    values = ['/admin', '/reviews', '/speaker']
    return values[0] if role else None
""",
    )

    clusters = advisory_literal_clusters(tmp_path)

    assert len(clusters) == 1
    assert clusters[0]["confidence"] == "inferred"
    assert clusters[0]["shared_values"] == ["/admin", "/reviews", "/speaker"]
