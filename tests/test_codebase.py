import pytest

from codegraph import (
    Codebase,
    FileAnalysis,
    FunctionNotFoundError,
    SourceFile,
    VbaFrontend,
)


def build_codebase() -> Codebase:
    return Codebase.analyze(
        [
            SourceFile(
                "frmOrder.frm",
                """
Private Sub bSave_Click()
    If ValidateOrder() Then
        Call SaveOrder
    End If
End Sub
""".lstrip(),
            ),
            SourceFile(
                "modOrder.bas",
                """
Public Function ValidateOrder() As Boolean
    ValidateOrder = LoadCustomer()
End Function

Public Sub SaveOrder()
End Sub

Public Function LoadCustomer() As Boolean
End Function
""".lstrip(),
            ),
        ],
        [VbaFrontend()],
    )


def test_vba_analysis_resolves_cross_file_and_forward_calls() -> None:
    codebase = build_codebase()

    save = "vba:frmOrder:bSave_Click"
    assert [item.id for item in codebase.callees(save)] == [
        "vba:modOrder:SaveOrder",
        "vba:modOrder:ValidateOrder",
    ]
    assert [item.id for item in codebase.callees(save, transitive=True)] == [
        "vba:modOrder:SaveOrder",
        "vba:modOrder:ValidateOrder",
        "vba:modOrder:LoadCustomer",
    ]
    assert codebase.function("vba:modOrder:ValidateOrder").source_range.start_line == 1


def test_context_entries_and_unresolved_calls_are_visible() -> None:
    codebase = Codebase.analyze(
        [
            SourceFile(
                "frmOrder.frm",
                """
Private Sub bSave_Click()
    Call MissingProcedure
End Sub
""".lstrip(),
            )
        ],
        [VbaFrontend()],
    )

    codebase.accept_entry_candidates()
    context = codebase.context_for("vba:frmOrder:bSave_Click")

    assert context.entry.kind == "form_event"
    assert [item.id for item in context.functions] == ["vba:frmOrder:bSave_Click"]
    assert context.calls == ()
    assert [item.code for item in context.diagnostics] == ["unresolved_call"]


def test_cycles_do_not_include_the_starting_function() -> None:
    codebase = Codebase.analyze(
        [
            SourceFile(
                "modCycle.bas",
                """
Public Sub First()
    Second
End Sub

Public Sub Second()
    First
End Sub
""".lstrip(),
            )
        ],
        [VbaFrontend()],
    )

    assert [item.id for item in codebase.callees("vba:modCycle:First", transitive=True)] == [
        "vba:modCycle:Second"
    ]


def test_refresh_finds_an_entry_affected_by_a_removed_function() -> None:
    codebase = build_codebase()
    codebase.set_entries(["vba:frmOrder:bSave_Click"], kind="form_event")

    result = codebase.refresh(
        [
            SourceFile(
                "modOrder.bas",
                """
Public Sub SaveOrder()
End Sub
""".lstrip(),
            )
        ]
    )

    assert "vba:modOrder:ValidateOrder" in result.changed_function_ids
    assert [entry.function_id for entry in result.affected_entry_points] == [
        "vba:frmOrder:bSave_Click"
    ]
    assert [item.id for item in codebase.callees("vba:frmOrder:bSave_Click")] == [
        "vba:modOrder:SaveOrder"
    ]


def test_unknown_function_has_one_clear_error_type() -> None:
    with pytest.raises(FunctionNotFoundError):
        build_codebase().function("vba:missing:Function")


def test_files_require_exactly_one_frontend() -> None:
    class CatchAllFrontend:
        def supports(self, file: SourceFile) -> bool:
            return True

        def analyze(self, files: tuple[SourceFile, ...]) -> FileAnalysis:
            return FileAnalysis()

    codebase = Codebase.analyze(
        [SourceFile("module.txt", "not VBA")],
        [CatchAllFrontend(), CatchAllFrontend()],
    )

    assert [item.code for item in codebase.diagnostics] == ["ambiguous_frontend"]
