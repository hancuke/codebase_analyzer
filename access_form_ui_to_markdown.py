"""Convert an exported Microsoft Access form's UI definition to Markdown.

Usage:
    python access_form_ui_to_markdown.py Form_Main.txt
    python access_form_ui_to_markdown.py Form_Main.txt --output Form_Main.md
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path


_TYPED_BEGIN = re.compile(r"^\s*Begin\s+([A-Za-z][A-Za-z0-9_]*)\s*$")
_BARE_BEGIN = re.compile(r"^\s*Begin\s*$")
_PROPERTY = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*=\s*(.*)$")
_PROCEDURE = re.compile(
    r"^\s*(?:Public|Private|Friend)?\s*(?:Static\s+)?"
    r"(?:Sub|Function)\s+([A-Za-z][A-Za-z0-9_]*)\b",
    re.IGNORECASE,
)
_DATA_PROPERTIES = ("RecordSource", "ControlSource", "RowSourceType", "RowSource")
_RULE_PROPERTIES = (
    "Visible",
    "Enabled",
    "Locked",
    "Required",
    "ValidationRule",
    "ValidationText",
    "DefaultValue",
    "AllowAdditions",
    "AllowDeletions",
    "AllowEdits",
    "Filter",
    "FilterOnLoad",
)
_CONTAINER_TYPES = frozenset(("Section", "Tab", "Page", "OptionGroup"))


@dataclass
class UiElement:
    """One typed Access UI block and its properties."""

    element_type: str
    properties: dict[str, str] = field(default_factory=dict)
    children: list[UiElement] = field(default_factory=list)


def read_access_export(path: Path) -> str:
    """Read an Access export encoded as UTF-8, UTF-16, or a Windows code page."""
    data = path.read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig")
    if b"\x00" in data[:1024]:
        encoding = "utf-16-le" if data[1::2].count(0) >= data[::2].count(0) else "utf-16-be"
        return data.decode(encoding)

    for encoding in ("utf-8", "gbk", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("access export", data, 0, len(data), "unsupported encoding")


def extract_ui_elements(text: str) -> list[UiElement]:
    """Extract typed UI blocks before the optional CodeBehindForm VBA section."""
    elements: list[UiElement] = []
    stack: list[UiElement | None] = []

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if line == "CodeBehindForm":
            break

        typed_begin = _TYPED_BEGIN.match(raw_line)
        if typed_begin:
            element = UiElement(typed_begin.group(1))
            elements.append(element)
            if parent := _current_element(stack):
                parent.children.append(element)
            stack.append(element)
            continue
        if _BARE_BEGIN.match(raw_line):
            stack.append(None)
            continue
        if line == "End":
            if not stack:
                raise ValueError(f"line {line_number}: unmatched End")
            stack.pop()
            continue

        property_match = _PROPERTY.match(raw_line)
        if property_match and stack:
            key, value = property_match.groups()
            if current := _current_element(stack):
                current.properties[key] = _unquote_access_value(value)
            if value == "Begin":
                stack.append(None)

    if stack:
        raise ValueError("unterminated Begin block in UI definition")
    return elements


def extract_vba_procedure_names(text: str) -> dict[str, str]:
    """Return case-insensitive procedure names declared after CodeBehindForm."""
    _, separator, code = text.partition("CodeBehindForm")
    if not separator:
        return {}
    procedures: dict[str, str] = {}
    for line in code.splitlines():
        match = _PROCEDURE.match(line)
        if match:
            procedures[match.group(1).casefold()] = match.group(1)
    return procedures


def ui_markdown(
    elements: list[UiElement],
    form_name: str,
    procedures: dict[str, str] | None = None,
) -> str:
    """Render relevant UI facts under their actual Access containers."""
    procedures = procedures or {}
    form = next((element for element in elements if element.element_type == "Form"), None)
    if form is None:
        raise ValueError("UI definition does not contain a Form block")
    markdown = [
        f"# {_escape_markdown(form_name)}",
        "",
        "## Form Metadata",
        "",
    ]
    if caption := form.properties.get("Caption"):
        markdown.append(f"- **Caption:** {_escape_markdown(caption)}")
    if "RecordSource" in form.properties:
        markdown.append(f"- **Record source:** `{form.properties['RecordSource']}`")
    form_details = "; ".join(
        detail
        for detail in (
            _event_details(form.properties, form_name, "Form", procedures),
            _joined_properties(form.properties, *_RULE_PROPERTIES),
        )
        if detail
    )
    if form_details:
        markdown.append(f"- **Behavior:** {form_details}")

    _render_container(
        form,
        level=2,
        markdown=markdown,
        procedures=procedures,
    )
    return "\n".join(markdown) + "\n"


def convert_file(input_path: Path) -> str:
    """Read an export file and return its UI definition as a Markdown table."""
    text = read_access_export(input_path)
    return ui_markdown(
        extract_ui_elements(text),
        input_path.stem,
        extract_vba_procedure_names(text),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract an Access form UI definition as a Markdown table."
    )
    parser.add_argument("input", type=Path, help="Access form export (.txt)")
    parser.add_argument(
        "-o", "--output", type=Path, help="Markdown output path (default: stdout)"
    )
    args = parser.parse_args()

    markdown = convert_file(args.input)
    if args.output is None:
        print(markdown, end="")
    else:
        args.output.write_text(markdown, encoding="utf-8")


def _unquote_access_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return value[1:-1].replace('""', '"')
    return value


def _current_element(stack: list[UiElement | None]) -> UiElement | None:
    return next((element for element in reversed(stack) if element is not None), None)


def _render_container(
    container: UiElement,
    *,
    level: int,
    markdown: list[str],
    procedures: dict[str, str],
) -> None:
    children = container.children
    leaves = (child for child in children if child.element_type not in _CONTAINER_TYPES)
    rows = [
        row
        for element in leaves
        if (row := _control_row(element, procedures)) is not None
    ]
    if rows:
        markdown.extend(
            [
                "",
                "| Control | Caption | Binding / Events / Rules |",
                "| --- | --- | --- |",
                *(_markdown_row(row) for row in rows),
            ]
        )

    for child in children:
        if child.element_type not in _CONTAINER_TYPES:
            continue
        heading = _container_heading(child)
        if heading is None:
            _render_container(
                child,
                level=level,
                markdown=markdown,
                procedures=procedures,
            )
            continue
        markdown.extend(
            [
                "",
                f"{'#' * min(level, 6)} {heading}",
            ]
        )
        _render_container(
            child,
            level=level + 1,
            markdown=markdown,
            procedures=procedures,
        )


def _control_row(
    element: UiElement,
    procedures: dict[str, str],
) -> tuple[str, str, str] | None:
    name = element.properties.get("Name")
    if not name:
        return None
    caption = _display_text(element.properties) or _nested_label_caption(element)
    return (
        f"{element.element_type} `{name}`",
        caption,
        _business_details(element.properties, name, element.element_type, procedures),
    )


def _nested_label_caption(element: UiElement) -> str:
    return next(
        (
            child.properties["Caption"]
            for child in element.children
            if child.element_type == "Label"
            and child.properties.get("Caption", "").strip()
        ),
        "",
    )


def _display_text(properties: dict[str, str]) -> str:
    return properties.get("StatusBarText", "") or properties.get("Caption", "")


def _container_heading(element: UiElement) -> str | None:
    title = _display_text(element.properties)
    name = element.properties.get("Name", "")
    if title:
        return _escape_markdown(title)
    if name:
        return f"{element.element_type} `{name}`"
    return None


def _joined_properties(
    properties: dict[str, str], *keys: str, separator: str = ", "
) -> str:
    values = [
        f"{key}={_display_property_value(properties[key])}"
        for key in keys
        if key in properties
    ]
    return separator.join(values)


def _business_details(
    properties: dict[str, str],
    control_name: str,
    element_type: str,
    procedures: dict[str, str],
    *,
    include_events: bool = True,
) -> str:
    details = [_joined_properties(properties, *_DATA_PROPERTIES)]
    if include_events:
        details.append(_event_details(properties, control_name, element_type, procedures))
    details.append(_joined_properties(properties, *_RULE_PROPERTIES))
    return "; ".join(detail for detail in details if detail)


def _event_details(
    properties: dict[str, str],
    control_name: str,
    element_type: str,
    procedures: dict[str, str],
) -> str:
    return "; ".join(
        _event_description(key, value, control_name, element_type, procedures)
        for key, value in properties.items()
        if key.startswith("On") and value
    )


def _event_description(
    event: str,
    value: str,
    control_name: str,
    element_type: str,
    procedures: dict[str, str],
) -> str:
    if value.casefold() != "[event procedure]":
        return f"{event}: {value}"
    procedure = (
        f"Form_{event.removeprefix('On')}"
        if element_type == "Form"
        else f"{control_name}_{event.removeprefix('On')}"
    )
    actual_name = procedures.get(procedure.casefold())
    if actual_name:
        return f"{event}: {actual_name}"
    return f"{event}: {procedure}"


def _display_property_value(value: str) -> str:
    return "non-default" if value == "NotDefault" else value


def _markdown_row(values: tuple[str, ...]) -> str:
    return "| " + " | ".join(_escape_markdown(value) or "-" for value in values) + " |"


def _escape_markdown(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


if __name__ == "__main__":
    main()
