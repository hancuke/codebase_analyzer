from pathlib import Path

from access_form_ui_to_markdown import (
    convert_file,
    extract_ui_elements,
    extract_vba_procedure_names,
    ui_markdown,
)


FORM_EXPORT = """\
Version =20
Begin Form
    Caption ="Order | Entry"
    RecordSource ="Orders"
    AllowEdits =0
    Begin Section
        Name ="Detail"
        Begin CommandButton
            Name ="SaveButton"
            StatusBarText ="Save the current order"
            OnClick ="[Event Procedure]"
            OnDblClick ="=AuditSave()"
            GUID = Begin
            End
            Begin
                Begin Label
                    Name ="SaveLabel"
                    Caption ="&Save"
                End
            End
        End
    End
End
CodeBehindForm
Private Sub SaveButton_Click()
End Sub
"""


def test_extracts_named_ui_elements_and_ignores_code_behind() -> None:
    markdown = ui_markdown(
        extract_ui_elements(FORM_EXPORT),
        "Form_Order",
        extract_vba_procedure_names(FORM_EXPORT),
    )

    assert "Position" not in markdown
    assert "Size" not in markdown
    assert markdown.startswith("# Form_Order\n")
    assert "## Form Metadata" in markdown
    assert "- **Caption:** Order \\| Entry" in markdown
    assert "**Name:**" not in markdown
    assert "- **Record source:** `Orders`" in markdown
    assert "- **Behavior:** AllowEdits=0" in markdown
    assert "## Section `Detail`" in markdown
    assert "| Control | Caption | Binding / Events / Rules |" in markdown
    assert (
        "| CommandButton `SaveButton` | Save the current order"
        " | OnClick: SaveButton_Click; OnDblClick: =AuditSave() |"
    )
    assert "SaveLabel" not in markdown


def test_uses_conventional_name_for_missing_event_procedure() -> None:
    markdown = ui_markdown(extract_ui_elements(FORM_EXPORT), "Form_Order")

    assert "OnClick: SaveButton_Click" in markdown
    assert "(unverified)" not in markdown


def test_uses_nested_label_caption_when_control_has_no_text() -> None:
    export = FORM_EXPORT.replace(
        '            StatusBarText ="Save the current order"\n',
        "",
    )

    markdown = ui_markdown(extract_ui_elements(export), "Form_Order")

    assert "| CommandButton `SaveButton` | &Save |" in markdown
    assert "SaveLabel" not in markdown


def test_ignores_anonymous_default_control_definitions() -> None:
    export = """\
Begin Form
    Begin TextBox
        FontSize =9
    End
    Begin Section
        Name ="Detail"
        Begin TextBox
            Name ="OrderId"
            ControlSource ="OrderId"
        End
    End
End
"""

    markdown = ui_markdown(extract_ui_elements(export), "Form_Order")

    assert "## TextBox" not in markdown
    assert "## Section `Detail`" in markdown
    assert "| TextBox `OrderId` | - | ControlSource=OrderId |" in markdown


def test_joins_wrapped_access_string_property_fragments() -> None:
    export = '''\
Begin Form
    Begin ComboBox
        Name ="srhRmNo"
        RowSourceType ="Table/Query"
        RowSource ="SELECT * FROM Q_AI_RM_NO_LOV WHERE APP_STATUS IN ('CMD', 'INF', 'TMN') AND AI_NO"
            " = ""BCHK"""
        FontName ="Arial"
    End
End
'''

    elements = extract_ui_elements(export)
    combo_box = next(
        element for element in elements if element.element_type == "ComboBox"
    )

    assert combo_box.properties["RowSource"] == (
        "SELECT * FROM Q_AI_RM_NO_LOV "
        "WHERE APP_STATUS IN ('CMD', 'INF', 'TMN') AND AI_NO = \"BCHK\""
    )
    assert combo_box.properties["FontName"] == "Arial"

    markdown = ui_markdown(elements, "Form_Search")
    assert (
        'RowSource=SELECT * FROM Q_AI_RM_NO_LOV WHERE APP_STATUS IN '
        "('CMD', 'INF', 'TMN') AND AI_NO = \"BCHK\""
    ) in markdown


def test_displays_row_source_line_escapes_as_spaces() -> None:
    export = r'''\
Begin Form
    Begin ComboBox
        Name ="Employee"
        RowSourceType ="Table/Query"
        RowSource ="SELECT ID, Name\015\012FROM Employee\015\012WHERE Active = True;"
    End
End
'''

    elements = extract_ui_elements(export)
    combo_box = next(
        element for element in elements if element.element_type == "ComboBox"
    )

    assert combo_box.properties["RowSource"] == (
        r"SELECT ID, Name\015\012FROM Employee\015\012WHERE Active = True;"
    )

    markdown = ui_markdown(elements, "Form_Employee")
    assert (
        "RowSource=SELECT ID, Name FROM Employee WHERE Active = True;"
    ) in markdown
    assert r"\015\012" not in markdown


def test_reads_utf16_export_and_writes_markdown(tmp_path: Path) -> None:
    input_path = tmp_path / "Form_Order.txt"
    input_path.write_text(FORM_EXPORT, encoding="utf-16")

    markdown = convert_file(input_path)

    assert "| Control | Caption | Binding / Events / Rules |" in markdown
    assert "| CommandButton `SaveButton` |" in markdown


def test_rejects_unmatched_ui_block() -> None:
    try:
        extract_ui_elements("Begin Form\nEnd\nEnd\n")
    except ValueError as error:
        assert str(error) == "line 3: unmatched End"
    else:
        raise AssertionError("expected malformed export to be rejected")
