"""Generate quiet, tree-derived rule/chokepoint audit findings.

Mechanical findings are release-gating because they are tied to registered
rules and executable calibration tests. Literal-vocabulary clusters are only
advisory discovery signals: shared values do not prove shared semantics.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STRUCTURAL_TEST_WORDS = frozenset({"wiring", "source", "declaration", "packaging"})
INSERT_PATTERN = re.compile(r"\bINSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)", re.I)
STRING_PATTERN = re.compile(r'''(?P<quote>["'])(?P<value>(?:\\.|(?!\1).)*)\1''')


@dataclass(frozen=True, slots=True)
class Finding:
    code: str
    confidence: str
    path: str
    line: int
    message: str
    user_path: str
    severity: str = "P2"


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _enclosing_function(tree: ast.AST, line: int) -> str:
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.lineno <= line <= (node.end_lineno or node.lineno)
    ]
    if not matches:
        return "<module>"
    return min(matches, key=lambda node: (node.end_lineno or node.lineno) - node.lineno).name


def writer_sites(root: Path, table: str) -> set[str]:
    sites: set[str] = set()
    for path in sorted((root / "src" / "sessionbuddy").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for match in INSERT_PATTERN.finditer(source):
            if match.group(1).lower() != table.lower():
                continue
            line = source.count("\n", 0, match.start()) + 1
            sites.add(f"{_relative(path, root)}:{_enclosing_function(tree, line)}")
    return sites


def writer_inventory_findings(root: Path, config: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    for table, registered_values in config.get("writer_inventories", {}).items():
        registered = set(registered_values)
        actual = writer_sites(root, table)
        for site in sorted(actual - registered):
            path, _ = site.rsplit(":", 1)
            findings.append(
                Finding(
                    code="unregistered_writer",
                    confidence="mechanical",
                    path=path,
                    line=1,
                    message=f"{table} has an unregistered production writer: {site}",
                    user_path="Persisted state can bypass the rule enforced by registered writers.",
                    severity="P1",
                )
            )
        for site in sorted(registered - actual):
            path, _ = site.rsplit(":", 1)
            findings.append(
                Finding(
                    code="missing_registered_writer",
                    confidence="mechanical",
                    path=path,
                    line=1,
                    message=f"registered {table} writer no longer exists: {site}",
                    user_path=(
                        "The generated writer inventory no longer describes the shipped path."
                    ),
                )
            )
    return findings


def _assigned_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
    return names


def _calls_read_text(node: ast.AST) -> bool:
    return any(
        isinstance(child, ast.Call)
        and isinstance(child.func, ast.Attribute)
        and child.func.attr == "read_text"
        for child in ast.walk(node)
    )


def _source_derived_names(function: ast.AST) -> set[str]:
    derived: set[str] = set()
    assignments = [
        node
        for node in ast.walk(function)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
    ]
    changed = True
    while changed:
        changed = False
        for assignment in assignments:
            value = assignment.value
            if value is None:
                continue
            source_derived = _calls_read_text(value) or bool(
                _assigned_names(value) & derived
            )
            if not source_derived:
                continue
            targets = (
                assignment.targets
                if isinstance(assignment, ast.Assign)
                else [assignment.target]
            )
            before = len(derived)
            for target in targets:
                derived.update(_assigned_names(target))
            changed = changed or len(derived) != before
    return derived


def source_test_name_findings(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in sorted((root / "tests").rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for function in (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
        ):
            assertions = [node for node in ast.walk(function) if isinstance(node, ast.Assert)]
            if not assertions:
                continue
            derived = _source_derived_names(function)
            # This is intentionally narrow. A source-derived value used in a
            # regex, parser, schema comparison, or calculation is not the
            # failure shape being gated. The mechanical rule covers only tests
            # made exclusively from literal membership assertions over source.
            exclusively_source = all(
                isinstance(assertion.test, ast.Compare)
                and len(assertion.test.ops) == 1
                and isinstance(assertion.test.ops[0], (ast.In, ast.NotIn))
                and (
                    _calls_read_text(assertion.test.comparators[0])
                    or bool(_assigned_names(assertion.test.comparators[0]) & derived)
                )
                for assertion in assertions
            )
            honest_name = any(word in function.name.lower() for word in STRUCTURAL_TEST_WORDS)
            if exclusively_source and not honest_name:
                findings.append(
                    Finding(
                        code="source_test_behavioral_name",
                        confidence="mechanical",
                        path=_relative(path, root),
                        line=function.lineno,
                        message=(
                            f"{function.name} proves source shape only; rename it with "
                            "wiring, source, declaration, or packaging"
                        ),
                        user_path=(
                            "A green source assertion can be mistaken for user-visible coverage."
                        ),
                    )
                )
    return findings


def _balanced_segment(source: str, start: int, opening: str, closing: str) -> str | None:
    depth = 0
    quote = ""
    escaped = False
    for index in range(start, len(source)):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {'"', "'", "`"}:
            quote = char
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    return None


def _top_level_object_keys(source: str) -> set[str]:
    keys: set[str] = set()
    depth = 0
    quote = ""
    escaped = False
    index = 0
    while index < len(source):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            index += 1
            continue
        if char in {'"', "'", "`"}:
            quote = char
        elif char in "{[(":
            depth += 1
        elif char in "}])":
            depth -= 1
        elif depth == 1 and (char.isalpha() or char in "_$"):
            match = re.match(r"[A-Za-z_$][A-Za-z0-9_$]*\s*:", source[index:])
            if match:
                keys.add(match.group(0).split(":", 1)[0].strip())
                index += len(match.group(0))
                continue
        index += 1
    return keys


def session_mock_findings(root: Path) -> list[Finding]:
    contract = json.loads((root / "openapi" / "openapi.json").read_text(encoding="utf-8"))
    schema = contract["components"]["schemas"]["SessionEventAccess"]
    allowed = set(schema["properties"])
    findings: list[Finding] = []
    pattern = re.compile(r"\bevent_access\s*:\s*\[")
    for path in sorted((root / "harness" / "e2e").rglob("*.ts")):
        source = path.read_text(encoding="utf-8")
        for match in pattern.finditer(source):
            bracket = source.find("[", match.start())
            array = _balanced_segment(source, bracket, "[", "]")
            if not array or "{" not in array:
                continue
            object_start = array.find("{")
            item = _balanced_segment(array, object_start, "{", "}")
            if not item:
                continue
            keys = _top_level_object_keys(item)
            line = source.count("\n", 0, match.start()) + 1
            for key in sorted(keys - allowed):
                findings.append(
                    Finding(
                        code="session_mock_unknown_event_access_field",
                        confidence="mechanical",
                        path=_relative(path, root),
                        line=line,
                        message=f"SessionEventAccess mock emits field absent from OpenAPI: {key}",
                        user_path=(
                            "Browser coverage can pass against a payload the server cannot emit."
                        ),
                        severity="P1",
                    )
                )
            # Missing-field analysis remains advisory until all session mocks
            # use the shared fixture builder. Unknown fields are unambiguously
            # impossible server output and are safe to gate immediately.
    return findings


def _literal_value(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        return tuple(sorted((_literal_value(item) for item in node.elts), key=repr))
    if isinstance(node, ast.Dict):
        return tuple(
            sorted(
                (
                    (_literal_value(key), _literal_value(value))
                    for key, value in zip(node.keys, node.values, strict=True)
                ),
                key=repr,
            )
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        return _literal_value(node.left) * _literal_value(node.right)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "frozenset"
        and len(node.args) == 1
    ):
        return _literal_value(node.args[0])
    raise ValueError("not a static literal")


def _module_assignments(path: Path) -> dict[str, tuple[Any, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    assignments: dict[str, tuple[Any, int]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if len(targets) != 1 or not isinstance(targets[0], ast.Name):
            continue
        try:
            assignments[targets[0].id] = (_literal_value(node.value), node.lineno)
        except (TypeError, ValueError):
            continue
    return assignments


def canonical_constant_findings(root: Path, config: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    all_assignments: dict[str, tuple[Any, int]] = {}
    for path in sorted((root / "src" / "sessionbuddy").rglob("*.py")):
        for symbol, value in _module_assignments(path).items():
            all_assignments[f"{_relative(path, root)}:{symbol}"] = value
    for declaration in config.get("canonical_constants", ()):
        canonical_id = f"{declaration['path']}:{declaration['symbol']}"
        if canonical_id not in all_assignments:
            findings.append(
                Finding(
                    code="canonical_constant_missing",
                    confidence="mechanical",
                    path=declaration["path"],
                    line=1,
                    message=f"registered canonical constant is missing: {canonical_id}",
                    user_path="Consumers no longer have the registered source of truth.",
                    severity="P1",
                )
            )
            continue
        canonical, _ = all_assignments[canonical_id]
        if not isinstance(canonical, tuple):
            continue
        canonical_entries = set(canonical)
        allowed = set(declaration.get("allowed_projections", ()))
        minimum = int(declaration.get("minimum_shared_entries", 2))
        for candidate_id, (candidate, line) in all_assignments.items():
            if (
                candidate_id == canonical_id
                or candidate_id in allowed
                or not isinstance(candidate, tuple)
            ):
                continue
            overlap = canonical_entries & set(candidate)
            if len(overlap) >= minimum:
                path, _ = candidate_id.rsplit(":", 1)
                findings.append(
                    Finding(
                        code="duplicate_canonical_constant",
                        confidence="mechanical",
                        path=path,
                        line=line,
                        message=(
                            f"{candidate_id} duplicates {len(overlap)} entries from "
                            f"canonical rule {declaration['id']}"
                        ),
                        user_path="Two policy tables can silently disagree on accepted input.",
                        severity="P1",
                    )
                )
    return findings


def persisted_enum_findings(root: Path, config: dict[str, Any]) -> list[Finding]:
    trees: dict[Path, ast.AST] = {}
    for path in sorted((root / "src" / "sessionbuddy").rglob("*.py")):
        trees[path] = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    findings: list[Finding] = []
    for declaration in config.get("persisted_enums", ()):
        path = root / declaration["path"]
        tree = trees.get(path)
        enum_node = next(
            (
                node
                for node in getattr(tree, "body", ())
                if isinstance(node, ast.ClassDef) and node.name == declaration["symbol"]
            ),
            None,
        )
        if enum_node is None:
            findings.append(
                Finding(
                    code="persisted_enum_missing",
                    confidence="mechanical",
                    path=declaration["path"],
                    line=1,
                    message=f"registered persisted enum is missing: {declaration['symbol']}",
                    user_path="Stored vocabulary no longer has its registered semantic owner.",
                    severity="P1",
                )
            )
            continue
        members = {
            target.id: node.lineno
            for node in enum_node.body
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
            if isinstance(target, ast.Name)
        }
        compared: set[str] = set()
        for candidate_tree in trees.values():
            for comparison in (
                node for node in ast.walk(candidate_tree) if isinstance(node, ast.Compare)
            ):
                for child in ast.walk(comparison):
                    if (
                        isinstance(child, ast.Attribute)
                        and isinstance(child.value, ast.Name)
                        and child.value.id == declaration["symbol"]
                    ):
                        compared.add(child.attr)
        for member in sorted(set(members) - compared):
            findings.append(
                Finding(
                    code="inert_persisted_enum_member",
                    confidence="mechanical",
                    path=declaration["path"],
                    line=members[member],
                    message=(
                        f"{declaration['symbol']}.{member} is persisted vocabulary but never "
                        "participates in a policy comparison"
                    ),
                    user_path="The product can store a choice that changes no supported behavior.",
                    severity="P1",
                )
            )
    return findings


def _trigger_fired(root: Path, trigger: dict[str, Any]) -> bool:
    kind = trigger.get("kind")
    path = root / str(trigger.get("path", ""))
    if kind == "path_exists":
        return path.exists()
    if kind == "path_absent":
        return not path.exists()
    if kind == "symbol_absent":
        return path.exists() and str(trigger.get("symbol", "")) not in path.read_text(
            encoding="utf-8"
        )
    if kind == "migration_count_at_least":
        return len(list(path.glob("*.sql"))) >= int(trigger.get("count", 0))
    raise ValueError(f"unknown declaration trigger: {kind}")


def declaration_findings(root: Path, config: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    for collection, code in (
        ("temporary_duplications", "temporary_duplication_triggered"),
        ("waivers", "waiver_triggered"),
    ):
        for declaration in config.get(collection, ()):
            missing = [
                field
                for field in ("id", "removal", "trigger", "owner", "user_path")
                if not declaration.get(field)
            ]
            if missing:
                findings.append(
                    Finding(
                        code="incomplete_exception_declaration",
                        confidence="mechanical",
                        path="rule_audit.json",
                        line=1,
                        message=f"{declaration.get('id', '<unnamed>')} lacks {', '.join(missing)}",
                        user_path="An unowned exception can become permanent drift.",
                        severity="P1",
                    )
                )
                continue
            if _trigger_fired(root, declaration["trigger"]):
                findings.append(
                    Finding(
                        code=code,
                        confidence="mechanical",
                        path=str(declaration.get("path", "rule_audit.json")),
                        line=1,
                        message=(
                            f"{declaration['id']} outlived its removal trigger: "
                            f"{declaration['removal']}"
                        ),
                        user_path=str(declaration["user_path"]),
                        severity="P1",
                    )
                )
    return findings


def _interesting_literal(value: str) -> bool:
    return (
        value == "\\"
        or value.startswith("/")
        or bool(re.fullmatch(r"(?:application|audio|font|image|text|video)/[^\s]+", value))
    )


def _python_literal_sets(root: Path) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for path in sorted((root / "src" / "sessionbuddy").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            values = {
                child.value
                for child in ast.walk(node)
                if isinstance(child, ast.Constant)
                and isinstance(child.value, str)
                and _interesting_literal(child.value)
            }
            if len(values) >= 2:
                result[f"{_relative(path, root)}:{node.name}"] = values
    return result


def _javascript_literal_sets(root: Path) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    function_pattern = re.compile(r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{")
    assignment_pattern = re.compile(
        r"\b(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>"
    )
    for path in sorted((root / "src" / "sessionbuddy").rglob("*.js")):
        source = path.read_text(encoding="utf-8")
        for pattern in (function_pattern, assignment_pattern):
            for match in pattern.finditer(source):
                body_start = source.find("{", match.end() - 1)
                if body_start < 0:
                    continue
                body = _balanced_segment(source, body_start, "{", "}")
                if body is None:
                    continue
                values = {
                    literal.group("value")
                    for literal in STRING_PATTERN.finditer(body)
                    if _interesting_literal(literal.group("value"))
                }
                if len(values) >= 2:
                    result[f"{_relative(path, root)}:{match.group(1)}"] = values
    return result


def advisory_literal_clusters(root: Path, minimum_overlap: int = 3) -> list[dict[str, Any]]:
    sets = {**_python_literal_sets(root), **_javascript_literal_sets(root)}
    identifiers = sorted(sets)
    clusters: list[dict[str, Any]] = []
    for index, left in enumerate(identifiers):
        for right in identifiers[index + 1 :]:
            overlap = sets[left] & sets[right]
            smaller = min(len(sets[left]), len(sets[right]))
            if len(overlap) >= minimum_overlap and len(overlap) / smaller >= 0.75:
                clusters.append(
                    {
                        "confidence": "inferred",
                        "left": left,
                        "right": right,
                        "shared_values": sorted(overlap),
                    }
                )
    return clusters


def mechanical_findings(root: Path, config: dict[str, Any]) -> list[Finding]:
    return sorted(
        [
            *source_test_name_findings(root),
            *writer_inventory_findings(root, config),
            *session_mock_findings(root),
            *canonical_constant_findings(root, config),
            *persisted_enum_findings(root, config),
            *declaration_findings(root, config),
        ],
        key=lambda finding: (finding.path, finding.line, finding.code),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--include-advisory", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    config_path = args.config or root / "rule_audit.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    findings = mechanical_findings(root, config)
    payload: dict[str, Any] = {
        "mechanical": [asdict(finding) for finding in findings],
    }
    if args.include_advisory:
        payload["advisory"] = advisory_literal_clusters(root)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for finding in findings:
            print(f"{finding.path}:{finding.line}: {finding.code}: {finding.message}")
        if args.include_advisory:
            print(f"advisory literal clusters: {len(payload['advisory'])}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
