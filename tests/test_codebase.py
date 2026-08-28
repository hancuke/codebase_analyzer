import pytest

from codegraph import (
    BaseFrontend,
    Codebase,
    ContextLimits,
    Diagnostic,
    FileAnalysis,
    Function,
    FunctionNotFoundError,
    RawCall,
    SourceFile,
    SourceFileNotFoundError,
    SourceRange,
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


def test_source_files_and_functions_can_be_queried_by_file() -> None:
    codebase = build_codebase()

    assert [source_file.path for source_file in codebase.source_files] == [
        "frmOrder.frm",
        "modOrder.bas",
    ]
    assert codebase.source_file("frmOrder.frm").content.startswith("Private Sub")
    assert [function.id for function in codebase.functions_in_file("modOrder.bas")] == [
        "vba:modOrder:LoadCustomer",
        "vba:modOrder:SaveOrder",
        "vba:modOrder:ValidateOrder",
    ]


def test_source_file_queries_distinguish_unknown_and_empty_files() -> None:
    codebase = Codebase.analyze(
        [SourceFile("empty.bas", "Option Explicit\n")],
        [VbaFrontend()],
    )

    assert codebase.functions_in_file("empty.bas") == ()
    with pytest.raises(SourceFileNotFoundError):
        codebase.source_file("missing.bas")
    with pytest.raises(SourceFileNotFoundError):
        codebase.functions_in_file("missing.bas")


def test_file_and_function_filters_are_deterministic() -> None:
    codebase = build_codebase()

    assert [item.path for item in codebase.find_source_files(extension=".BAS")] == [
        "modOrder.bas"
    ]
    assert [item.id for item in codebase.find_functions(name="SaveOrder")] == [
        "vba:modOrder:SaveOrder"
    ]
    assert [item.id for item in codebase.functions_at("modOrder.bas", 5)] == [
        "vba:modOrder:SaveOrder"
    ]


def test_call_resolution_filters_and_reverse_call_facts() -> None:
    codebase = Codebase.analyze(
        [
            SourceFile(
                "modCalls.bas",
                """
Public Sub Main()
    Call SaveOrder
    Call MissingProcedure
End Sub

Public Sub SaveOrder()
End Sub
""".lstrip(),
            )
        ],
        [VbaFrontend()],
    )

    assert [call.name for call in codebase.calls_from(
        "vba:modCalls:Main", resolution="resolved"
    )] == ["SaveOrder"]
    assert [call.name for call in codebase.calls_from(
        "vba:modCalls:Main", resolution="unresolved"
    )] == ["MissingProcedure"]
    assert [call.source_id for call in codebase.calls_to("vba:modCalls:SaveOrder")] == [
        "vba:modCalls:Main"
    ]


def test_context_limits_report_truncation() -> None:
    codebase = build_codebase()
    context = codebase.context_for(
        "vba:frmOrder:bSave_Click",
        limits=ContextLimits(max_depth=1, max_functions=2),
    )

    assert context.truncated is True
    assert context.truncation_reasons == ("max_functions",)
    assert len(context.functions) == 2


def test_entries_can_be_removed_by_id_or_kind() -> None:
    codebase = build_codebase()
    codebase.accept_entry_candidates()
    codebase.mark_entries(lambda function: function.name == "SaveOrder", kind="manual")

    codebase.remove_entries(["vba:frmOrder:bSave_Click"])
    assert [entry.function_id for entry in codebase.entry_points] == [
        "vba:modOrder:SaveOrder"
    ]
    codebase.remove_entries(kind="manual")
    assert codebase.entry_points == ()


def test_refresh_updates_source_file_function_queries() -> None:
    codebase = build_codebase()

    codebase.refresh(
        [
            SourceFile(
                "modOrder.bas",
                """
Public Sub SaveOrder()
End Sub
""".lstrip(),
            )
        ],
        removed_paths=["frmOrder.frm"],
    )

    assert [source_file.path for source_file in codebase.source_files] == ["modOrder.bas"]
    assert [function.id for function in codebase.functions_in_file("modOrder.bas")] == [
        "vba:modOrder:SaveOrder"
    ]
    with pytest.raises(SourceFileNotFoundError):
        codebase.functions_in_file("frmOrder.frm")


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


def test_base_frontend_pipeline_template() -> None:
    class DummyPyFrontend(BaseFrontend):
        language_name = "python"
        file_extensions = {".py"}

        def extract_functions(
            self, source_file: SourceFile
        ) -> tuple[list[Function], list[Diagnostic]]:
            f1 = Function(
                id="python:main:run",
                name="run",
                language="python",
                module="main",
                file=source_file.path,
                source="def run(): helper()",
                source_range=SourceRange(1, 1),
            )
            f2 = Function(
                id="python:main:helper",
                name="helper",
                language="python",
                module="main",
                file=source_file.path,
                source="def helper(): pass",
                source_range=SourceRange(2, 2),
            )
            return [f1, f2], []

        def extract_raw_calls(self, function: Function) -> list[RawCall]:
            if function.name == "run":
                return [RawCall(name="helper", line=1)]
            return []

    codebase = Codebase.analyze(
        [SourceFile("main.py", "def run(): helper()\ndef helper(): pass")],
        [DummyPyFrontend()],
    )

    assert [item.id for item in codebase.callees("python:main:run")] == [
        "python:main:helper"
    ]


def test_vba_token_call_extraction_ignores_comments_and_strings() -> None:
    codebase = Codebase.analyze(
        [
            SourceFile(
                "modCalls.bas",
                """
Public Sub Main()
    Call SaveOrder
    SaveOrder order
    result = ValidateOrder()
    ' Call IgnoredComment
    message = "Call IgnoredString"
End Sub

Public Sub SaveOrder()
End Sub

Public Function ValidateOrder() As Boolean
End Function
""".lstrip(),
            )
        ],
        [VbaFrontend()],
    )

    calls = codebase.calls_from("vba:modCalls:Main")
    assert [(call.name, call.line) for call in calls] == [
        ("SaveOrder", 2),
        ("SaveOrder", 3),
        ("ValidateOrder", 4),
    ]
