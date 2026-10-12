from dataclasses import replace

import pytest

from codegraph import (
    BaseAnalyzer,
    Codebase,
    ContextLimits,
    Diagnostic,
    AnalysisResult,
    Function,
    FunctionNotFoundError,
    EntryPoint,
    RawCall,
    SourceFile,
    SourceFileNotFoundError,
    SourceRange,
    VbaAnalyzer,
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
        [VbaAnalyzer()],
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
        [VbaAnalyzer()],
    )

    context = codebase.dependency_context("vba:frmOrder:bSave_Click")

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
        [VbaAnalyzer()],
    )

    assert [item.id for item in codebase.callees("vba:modCycle:First", transitive=True)] == [
        "vba:modCycle:Second"
    ]


def test_unknown_function_has_one_clear_error_type() -> None:
    with pytest.raises(FunctionNotFoundError):
        build_codebase().function("vba:missing:Function")


def test_source_files_and_functions_can_be_queried_by_file() -> None:
    codebase = build_codebase()

    assert [source_file.source_id for source_file in codebase.source_files] == [
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
        [VbaAnalyzer()],
    )

    assert codebase.functions_in_file("empty.bas") == ()
    with pytest.raises(SourceFileNotFoundError):
        codebase.source_file("missing.bas")
    with pytest.raises(SourceFileNotFoundError):
        codebase.functions_in_file("missing.bas")


def test_file_and_function_filters_are_deterministic() -> None:
    codebase = build_codebase()

    assert [item.source_id for item in codebase.find_source_files(extension=".BAS")] == [
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
        [VbaAnalyzer()],
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
    context = codebase.dependency_context(
        "vba:frmOrder:bSave_Click",
        limits=ContextLimits(max_depth=1, max_functions=2),
    )

    assert context.truncated is True
    assert context.truncation_reasons == ("max_functions",)
    assert len(context.functions) == 2


def test_files_require_exactly_one_analyzer() -> None:
    class CatchAllAnalyzer:
        def supports(self, file: SourceFile) -> bool:
            return True

        def analyze(self, files: tuple[SourceFile, ...]) -> AnalysisResult:
            return AnalysisResult()

    codebase = Codebase.analyze(
        [SourceFile("module.txt", "not VBA")],
        [CatchAllAnalyzer(), CatchAllAnalyzer()],
    )

    assert [item.code for item in codebase.diagnostics] == ["ambiguous_analyzer"]


def test_duplicate_sources_and_invalid_entry_points_are_diagnosed() -> None:
    class FactAnalyzer:
        def supports(self, file: SourceFile) -> bool:
            return True

        def analyze(self, files: tuple[SourceFile, ...]) -> AnalysisResult:
            return AnalysisResult(
                entry_points=(
                    EntryPoint(
                        function_id="missing",
                        kind="structural",
                        source="analyzer",
                    ),
                )
            )

    codebase = Codebase.analyze(
        [SourceFile("same.bas", ""), SourceFile("same.bas", "")],
        [FactAnalyzer()],
    )

    assert codebase.entry_points == ()
    assert [item.code for item in codebase.diagnostics] == [
        "missing_entry_point",
        "duplicate_source_id",
    ]


def test_base_analyzer_pipeline_template() -> None:
    class DummyPyAnalyzer(BaseAnalyzer):
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
                source_id=source_file.source_id,
                source="def run(): helper()",
                source_range=SourceRange(1, 1),
            )
            f2 = Function(
                id="python:main:helper",
                name="helper",
                language="python",
                module="main",
                source_id=source_file.source_id,
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
        [DummyPyAnalyzer()],
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
        [VbaAnalyzer()],
    )

    calls = codebase.calls_from("vba:modCalls:Main")
    assert [(call.name, call.line) for call in calls] == [
        ("SaveOrder", 2),
        ("SaveOrder", 3),
        ("ValidateOrder", 4),
    ]


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_vba_call_lines_after_blank_lines_comments_and_strings(newline: str) -> None:
    source = newline.join(
        [
            "",
            "Public Sub Main()",
            "",
            "    ' Call Ignored",
            '    message = "Ignored()"',
            "",
            "    Call Helper",
            "    result = Helper()",
            "End Sub",
            "",
            "Public Function Helper()",
            "End Function",
        ]
    )
    codebase = Codebase.analyze(
        [SourceFile("modCalls.bas", source)], [VbaAnalyzer()]
    )

    calls = codebase.calls_from("vba:modCalls:Main")
    assert [(call.name, call.line) for call in calls] == [
        ("Helper", 7),
        ("Helper", 8),
    ]


class _CountingVbaAnalyzer(VbaAnalyzer):
    def __init__(self, *, cache_size: int = 0) -> None:
        self.function_parses = 0
        self.call_parses = 0
        super().__init__(cache_size=cache_size)

    def _parse_functions(
        self, source_file: SourceFile
    ) -> tuple[tuple[Function, ...], tuple[Diagnostic, ...]]:
        self.function_parses += 1
        return super()._parse_functions(source_file)

    def _parse_raw_calls(
        self, source: str, start_line: int
    ) -> tuple[tuple[str, int], ...]:
        self.call_parses += 1
        return super()._parse_raw_calls(source, start_line)


@pytest.mark.parametrize(("cache_size", "expected_parses"), [(0, 4), (1, 3), (2, 2)])
def test_vba_cache_is_bounded_instance_local_and_clearable(
    cache_size: int, expected_parses: int
) -> None:
    analyzer = _CountingVbaAnalyzer(cache_size=cache_size)
    first = SourceFile("first.bas", "Sub First()\nCall Missing\nEnd Sub\n")
    second = SourceFile("second.bas", "Sub Second()\nCall Missing\nEnd Sub\n")
    snapshots = [
        Codebase.analyze([source], [analyzer])
        for source in (first, second, first, first)
    ]
    assert analyzer.function_parses == expected_parses
    assert analyzer.call_parses == expected_parses

    independent = _CountingVbaAnalyzer(cache_size=cache_size)
    Codebase.analyze([first], [independent])
    assert independent.function_parses == independent.call_parses == 1

    analyzer.clear_cache()
    rebuilt = Codebase.analyze([first], [analyzer])
    assert analyzer.function_parses == expected_parses + 1
    assert analyzer.call_parses == expected_parses + 1
    assert rebuilt.functions == snapshots[0].functions
    assert rebuilt.diagnostics == snapshots[0].diagnostics


def test_vba_cache_keeps_symbol_resolution_independent() -> None:
    analyzer = _CountingVbaAnalyzer(cache_size=16)
    caller = SourceFile("caller.bas", "Sub Run_Click()\nCall Helper\nEnd Sub\n")
    helper = SourceFile("helper.bas", "Sub Helper()\nEnd Sub\n")
    duplicate = SourceFile("other.bas", helper.content)
    resolved = Codebase.analyze([caller, helper], [analyzer])
    unresolved = Codebase.analyze([caller], [analyzer])
    ambiguous = Codebase.analyze([caller, helper, duplicate], [analyzer])
    resolved_again = Codebase.analyze([caller, helper], [analyzer])

    assert analyzer.function_parses == 3
    assert analyzer.call_parses == 2
    assert resolved.calls_from("vba:caller:Run_Click")[0].target_id == "vba:helper:Helper"
    assert unresolved.calls_from("vba:caller:Run_Click")[0].target_id is None
    assert ambiguous.calls_from("vba:caller:Run_Click")[0].target_id is None
    assert "no indexed" in unresolved.diagnostics[0].message
    assert "more than one" in ambiguous.diagnostics[0].message
    assert resolved_again.calls_from("vba:caller:Run_Click") == resolved.calls_from(
        "vba:caller:Run_Click"
    )
    assert resolved_again.diagnostics == ()


def test_vba_cache_handles_content_identity_and_line_changes() -> None:
    analyzer = _CountingVbaAnalyzer(cache_size=16)
    source = SourceFile("caller.bas", "Sub Run()\nCall First\nEnd Sub\n")
    original = Codebase.analyze([source], [analyzer])
    modified = Codebase.analyze(
        [replace(source, content=source.content.replace("First", "Second"))],
        [analyzer],
    )
    shifted = Codebase.analyze(
        [replace(source, content="\n\n" + source.content)], [analyzer]
    )
    renamed = Codebase.analyze([replace(source, source_id="renamed.bas")], [analyzer])
    broken = replace(source, content="Sub Run()\n")
    for _ in range(2):
        functions, diagnostics = analyzer.extract_functions(broken)
        assert functions == []
        assert diagnostics[0].code == "unterminated_procedure"
    assert analyzer.function_parses == 5
    assert analyzer.call_parses == 3
    assert [(call.name, call.line) for call in original.calls_from("vba:caller:Run")] == [
        ("First", 2)
    ]
    assert modified.calls_from("vba:caller:Run")[0].name == "Second"
    assert shifted.calls_from("vba:caller:Run")[0].line == 4
    assert renamed.functions[0].source_id == "renamed.bas"
    assert renamed.calls_from("vba:renamed:Run")[0].name == "First"


def test_vba_cache_does_not_expose_mutable_cached_results() -> None:
    analyzer = _CountingVbaAnalyzer(cache_size=4)
    source = SourceFile("caller.bas", "Sub Run()\nCall Missing\nEnd Sub\nSub Broken()\n")
    functions, diagnostics = analyzer.extract_functions(source)
    function = functions[0]
    functions.clear()
    diagnostics.clear()
    calls = analyzer.extract_raw_calls(function)
    assert isinstance(calls[0].context, dict)
    calls[0].context["caller_mutation"] = True
    calls.clear()

    functions, diagnostics = analyzer.extract_functions(source)
    assert functions == [function]
    assert diagnostics[0].code == "unterminated_procedure"
    assert analyzer.extract_raw_calls(function) == [RawCall("Missing", 2)]
    assert analyzer.function_parses == analyzer.call_parses == 1


@pytest.mark.parametrize("cache_size", [-1, True, 1.5, None])
def test_vba_cache_rejects_invalid_limits(cache_size) -> None:
    error = ValueError if cache_size == -1 else TypeError
    with pytest.raises(error, match="cache_size must be a non-negative integer"):
        VbaAnalyzer(cache_size=cache_size)
