from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"
VOID_ELEMENTS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


@dataclass
class Element:
    tag: str
    attrs: dict[str, str | None]
    line: int
    parent: Element | None = None
    children: list[Element | str] = field(default_factory=list)


class TreeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Element("document", {}, 0)
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        element = Element(tag, dict(attrs), self.getpos()[0], self.stack[-1])
        self.stack[-1].children.append(element)
        if tag not in VOID_ELEMENTS:
            self.stack.append(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        element = Element(tag, dict(attrs), self.getpos()[0], self.stack[-1])
        self.stack[-1].children.append(element)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def elements(root: Element):
    for child in root.children:
        if isinstance(child, Element):
            yield child
            yield from elements(child)


def class_names(element: Element) -> set[str]:
    return set((element.attrs.get("class") or "").split())


def closest(element: Element, tag: str) -> Element | None:
    current = element.parent
    while current is not None:
        if current.tag == tag:
            return current
        current = current.parent
    return None


def test_static_required_markers_stay_inline_with_their_label_text() -> None:
    markers: list[tuple[Path, Element]] = []
    for path in sorted(STATIC.rglob("*.html")):
        parser = TreeParser()
        parser.feed(path.read_text(encoding="utf-8"))
        markers.extend(
            (path, element)
            for element in elements(parser.root)
            if "required-marker" in class_names(element)
        )

    assert markers, "No static required markers were found"
    failures: list[str] = []
    for path, marker in markers:
        location = f"{path.relative_to(STATIC)}:{marker.line}"
        if marker.tag != "span":
            failures.append(f"{location}: required marker must use an inline <span>")
        if marker.attrs.get("aria-hidden") != "true":
            failures.append(f'{location}: required marker must use aria-hidden="true"')
        if any(isinstance(child, Element) for child in marker.children):
            failures.append(f"{location}: required marker must not contain block/element children")
        if "".join(child for child in marker.children if isinstance(child, str)).strip() != "*":
            failures.append(f'{location}: required marker text must be exactly "*"')

        parent = marker.parent
        if parent is None:
            failures.append(f"{location}: required marker has no text container")
            continue
        marker_index = parent.children.index(marker)
        previous = parent.children[marker_index - 1] if marker_index else None
        if not isinstance(previous, str) or not previous.strip():
            failures.append(
                f"{location}: place the marker directly after label text, not as a sibling"
            )
        elif "\n" in previous[len(previous.rstrip()) :]:
            failures.append(f"{location}: required marker must stay on the label-text line")

        if closest(marker, "label") is not None and (
            parent.tag != "span" or "field-label" not in class_names(parent)
        ):
            failures.append(
                f"{location}: form marker must be inside the label's inline .field-label span"
            )

    assert not failures, "Required-marker structure errors:\n" + "\n".join(failures)


def test_every_static_required_control_has_a_label_and_marker_support() -> None:
    failures: list[str] = []
    for path in sorted(STATIC.rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        parser = TreeParser()
        parser.feed(text)
        page_elements = list(elements(parser.root))
        labels_by_for = {
            element.attrs["for"]: element
            for element in page_elements
            if element.tag == "label" and element.attrs.get("for")
        }
        controls = [
            element
            for element in page_elements
            if element.tag in {"input", "select", "textarea"}
            and "required" in element.attrs
        ]
        for control in controls:
            label = closest(control, "label")
            if label is None and control.attrs.get("id"):
                label = labels_by_for.get(control.attrs["id"])
            if label is None:
                failures.append(
                    f"{path.relative_to(STATIC)}:{control.line}: required "
                    f"{control.tag} has no associated label"
                )
        if controls and 'class="required-marker"' not in text and "api-client.js" not in text:
            failures.append(
                f"{path.relative_to(STATIC)}: required controls have no marker source"
            )

    assert not failures, "Required-control errors:\n" + "\n".join(failures)


def test_runtime_required_markers_wrap_text_for_nested_and_for_labels() -> None:
    client = (STATIC / "api_client.js").read_text(encoding="utf-8")
    assert 'control.closest("label")' in client
    assert 'document.querySelector(`label[for="${escaped}"]`)' in client
    assert 'wrapper.className = "field-label";' in client
    assert "requiredLabel(label, control).append(marker);" in client
    assert "label.insertBefore(wrapper, controlBranch);" in client
    assert "label.append(wrapper);" in client
    assert "controlBranch.after(wrapper);" in client
    assert "root.matches?.(selector) ? [root] : []" in client

    public_cfp = (STATIC / "public_cfp.js").read_text(encoding="utf-8")
    render_fields = public_cfp.split("function renderFields(fields, conditions) {", 1)[1]
    assert 'const fieldLabel = make("span", field.label, "field-label");' in render_fields
    assert "fieldLabel.append(marker);" in render_fields
    assert "label.append(fieldLabel);" in render_fields

    stylesheet = (STATIC / "product.css").read_text(encoding="utf-8")
    field_label_rule = stylesheet.split(".field-label {", 1)[1].split("}", 1)[0]
    assert "display: flex;" in field_label_rule
    marker_group_rule = stylesheet.split(".required-marker-group {", 1)[1].split(
        "}", 1
    )[0]
    assert "white-space: nowrap;" in marker_group_rule
