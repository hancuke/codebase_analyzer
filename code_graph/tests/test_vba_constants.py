from dataclasses import FrozenInstanceError, replace

import pytest

from codegraph import Codebase, SourceFile, VbaAnalyzer


def analyze(*files: SourceFile, analyzer: VbaAnalyzer | None = None) -> Codebase:
    return Codebase.analyze(files, [analyzer or VbaAnalyzer()])


def names(function) -> list[str]:
    return [item.name for item in function.constants]


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_direct_constants_preserve_declarations_and_function_source(newline) -> None:
    source = newline.join([
        'Private Const PREFIX As String = "Order"',
        "Public Const RETRIES As Long = 3",
        "Const UNUSED = 99",
        "Private Const A = 1",
        "Private Const B = A+1",
        "Public Sub Save()",
        "    Debug.Print PREFIX, RETRIES, B, PREFIX",
        "End Sub",
        "",
    ])
    codebase = analyze(SourceFile("A.bas", source))
    function = codebase.function("vba:A:Save")

    assert names(function) == ["PREFIX", "RETRIES", "B"]
    assert function.source == newline.join(source.split(newline)[5:])
    assert function.source_range.start_line == 6
    prefix, retries, expression = function.constants
    assert prefix.source == source.splitlines(keepends=True)[0]
    assert prefix.type_name == "String"
    assert prefix.expression == '"Order"'
    assert prefix.visibility == "private"
    assert retries.visibility == "public"
    assert expression.expression == "A+1"
    assert expression.source_range.start_line == 5


def test_cross_module_visibility_and_qualified_names_are_deterministic() -> None:
    files = (
        SourceFile("A.bas", "Private Const HIDDEN = 1\nConst DEFAULT_PRIVATE = 2\n"
                   "Public Const COUNT = 3\nPublic Const FLAG = True\n"),
        SourceFile("B.bas", "Public Const COUNT = 4\n"),
        SourceFile("C.cls", "Public Const CLASS_VALUE = 9\n"),
        SourceFile("F.frm", "Public Const FORM_VALUE = 9\n"),
        SourceFile("Use.bas", "Sub Run()\n"
                   "Debug.Print a.count, flag, HIDDEN, DEFAULT_PRIVATE\n"
                   "Debug.Print CLASS_VALUE, FORM_VALUE, obj.COUNT, A.HIDDEN\n"
                   "End Sub\n"),
    )
    first = analyze(*files)
    second = analyze(*reversed(files))
    function = first.function("vba:Use:Run")
    assert names(function) == ["COUNT", "FLAG"]
    assert function == second.function(function.id)
    assert first.diagnostics == second.diagnostics


def test_local_constants_win_and_local_declarations_shadow_globals() -> None:
    module = SourceFile(
        "Local.bas",
        "Const VALUE = 1\nPublic Const MODULE_NAME = 2\n"
        "Sub Run(ByVal PARAM As Long, Optional OTHER As Long = 0)\n"
        "Dim VARIABLE As Long, ARRAY() As String\n"
        "Static CACHE As Long\nConst LOCAL_VALUE = 7\n"
        "Debug.Print VALUE, PARAM, OTHER, VARIABLE, ARRAY, CACHE, LOCAL_VALUE\n"
        "End Sub\n",
    )
    public = SourceFile(
        "Public.bas",
        "\n".join(f"Public Const {name} = 99" for name in (
            "VALUE", "PARAM", "OTHER", "VARIABLE", "ARRAY", "CACHE", "LOCAL_VALUE"
        )),
    )
    assert names(analyze(module, public).function("vba:Local:Run")) == ["VALUE"]


def test_strings_comments_labels_named_arguments_and_members_are_not_uses() -> None:
    codebase = analyze(SourceFile(
        "A.bas",
        "Const TOKEN = 1\nSub Run(ByVal A As Object)\n"
        '\' TOKEN\nRem TOKEN: Debug.Print TOKEN\n'
        'Debug.Print "TOKEN ""TOKEN""", obj.TOKEN, A.TOKEN, obj!TOKEN\n'
        "TOKEN:\nGoTo TOKEN\n"
        "Call Helper(TOKEN:=0)\n"
        "With obj\nDebug.Print .TOKEN\nEnd With\n"
        "End Sub\n",
    ))
    function = codebase.function("vba:A:Run")
    assert function.constants == ()


def test_multiconstant_continuation_preserves_original_declaration() -> None:
    codebase = analyze(SourceFile(
        "A.bas",
        'Private Const A As String = "x,y:z", _\n'
        '    B = 2\n'
        "Sub Run()\nDebug.Print A, B\nEnd Sub\n",
    ))
    function = codebase.function("vba:A:Run")
    assert names(function) == ["A", "B"]
    assert function.constants[0].source_range.end_line == 2
    assert function.constants[0].expression == '"x,y:z"'
    assert function.constants[0].source == function.constants[1].source
    assert "    B = 2\n" in function.constants[0].source


def test_ambiguous_and_unknown_module_references_are_diagnosed_not_attached() -> None:
    codebase = analyze(
        SourceFile("A.bas", "Public Const CLASH = 1\n"),
        SourceFile("B.bas", "Public Const CLASH = 2\n"),
        SourceFile("unknown.txt", "Public Const UNKNOWN = 3\n", language="vba"),
        SourceFile("Use.bas", "Sub Run()\nDebug.Print CLASH, UNKNOWN\nEnd Sub\n"),
    )
    function = codebase.function("vba:Use:Run")
    assert function.constants == ()
    diagnostics = [item for item in codebase.diagnostics if item.function_id == function.id]
    assert {item.code for item in diagnostics} == {
        "ambiguous_constant", "unknown_constant_module",
    }
    assert all(item.line == 2 for item in diagnostics)


def test_txt_module_exports_and_instance_local_constants() -> None:
    codebase = analyze(
        SourceFile("Module_Settings.txt", "Public Const LIMIT = 2\n", language="vba"),
        SourceFile("Form_Use.txt", "Private Const CAPTION = \"Go\"\n"
                   "Sub Run_Click()\nDebug.Print LIMIT, CAPTION\nEnd Sub\n", language="vba"),
    )
    assert names(codebase.function("vba:Form_Use:Run_Click")) == ["CAPTION", "LIMIT"]


def test_conditional_and_malformed_constants_emit_diagnostics() -> None:
    codebase = analyze(SourceFile(
        "A.bas",
        "#If VBA7 Then\nPublic Const VALUE = 1\n#Else\n"
        "Public Const VALUE = 2\n#End If\n"
        "Private Const BROKEN\nSub Run()\nDebug.Print VALUE\nEnd Sub\n",
    ))
    assert codebase.function("vba:A:Run").constants == ()
    assert [item.code for item in codebase.diagnostics] == [
        "unsupported_constant_declaration",
        "unsupported_constant_declaration",
        "unsupported_constant_declaration",
        "unresolved_constant",
    ]


def test_procedure_local_constants_are_not_module_declarations() -> None:
    codebase = analyze(SourceFile(
        "A.bas",
        "Property Get Value() As Long\nConst LOCAL = 1\nEnd Property\n"
        "Sub Run()\nConst INNER = 2\nDebug.Print INNER, LOCAL\nEnd Sub\n",
    ))
    assert codebase.function("vba:A:Run").constants == ()


def test_constants_are_immutable_and_resolution_is_not_cached_between_batches() -> None:
    analyzer = VbaAnalyzer(cache_size=16)
    caller = SourceFile("Use.bas", "Sub Run()\nDebug.Print LIMIT\nEnd Sub\n")
    module = SourceFile("Settings.bas", "Public Const LIMIT = 2\n")
    first = analyze(caller, module, analyzer=analyzer).function("vba:Use:Run")
    second = analyze(caller, replace(module, content="Public Const LIMIT = 3\n"),
                     analyzer=analyzer).function(first.id)
    private = analyze(caller, replace(module, content="Private Const LIMIT = 3\n"),
                      analyzer=analyzer).function(first.id)
    missing = analyze(caller, analyzer=analyzer).function(first.id)
    assert first.constants[0].expression == "2"
    assert second.constants[0].expression == "3"
    assert private.constants == missing.constants == ()
    with pytest.raises(FrozenInstanceError):
        first.constants[0].expression = "4"
    mutable = list(first.constants)
    copied = replace(first, constants=mutable)
    mutable.clear()
    assert copied.constants == first.constants
    analyzer.clear_cache()
    assert analyze(caller, module, analyzer=analyzer).function(first.id) == first


def test_constants_do_not_change_call_graph_or_source() -> None:
    source = SourceFile("A.bas", "Const LIMIT = 2\n"
                        "Sub Run()\nCall Helper\nDebug.Print LIMIT\nEnd Sub\n"
                        "Sub Helper()\nDebug.Print LIMIT\nEnd Sub\n")
    codebase = analyze(source)
    function = codebase.function("vba:A:Run")
    helper = codebase.function("vba:A:Helper")
    assert codebase.callees(function.id) == (helper,)
    assert function.constants[0] is helper.constants[0]
    assert [item.id for item in codebase.dependency_context(function.id).functions] == [
        helper.id, function.id,
    ]


def test_default_parameters_and_local_initializers_reference_module_constants() -> None:
    codebase = analyze(SourceFile(
        "A.bas",
        "Private Const LIMIT = 2\nPrivate Const FLAG = True\n"
        "Sub Run(Optional ByVal Count As Long = LIMIT)\n"
        "Const LOCAL = FLAG\nEnd Sub\n",
    ))
    assert names(codebase.function("vba:A:Run")) == ["LIMIT", "FLAG"]


def test_unsupported_public_declarations_do_not_allow_false_unique_resolution() -> None:
    codebase = analyze(
        SourceFile("A.bas", "#If VBA7 Then\nPublic Const LIMIT = 2\n#End If\n"),
        SourceFile("B.bas", "Public Const LIMIT = 3\n"),
        SourceFile("Use.bas", "Sub Run()\nDebug.Print LIMIT\nEnd Sub\n"),
    )
    assert codebase.function("vba:Use:Run").constants == ()
    assert "unresolved_constant" in [item.code for item in codebase.diagnostics]


def test_duplicate_constant_and_module_names_are_not_resolved() -> None:
    duplicate = analyze(SourceFile(
        "A.bas", "Const LIMIT = 1\nConst LIMIT = 2\n"
        "Sub Run()\nDebug.Print LIMIT\nEnd Sub\n",
    ))
    assert duplicate.function("vba:A:Run").constants == ()
    assert {item.code for item in duplicate.diagnostics} == {
        "duplicate_constant", "ambiguous_constant",
    }
    modules = analyze(
        SourceFile("a/Settings.bas", "Public Const LIMIT = 1\n"),
        SourceFile("b/Settings.bas", "Public Const OTHER = 2\n"),
        SourceFile("Use.bas", "Sub Run()\nDebug.Print Settings.LIMIT\nEnd Sub\n"),
    )
    assert modules.function("vba:Use:Run").constants == ()
    assert "ambiguous_constant" in [item.code for item in modules.diagnostics]


def test_numeric_literals_and_return_array_type_are_not_identifier_references() -> None:
    codebase = analyze(SourceFile(
        "A.bas", "Const HFF = 2\nConst O77 = 3\nConst E3 = 4\n"
        "Const LIMIT = 5\nFunction Run(Optional Count As Long = LIMIT) As String()\n"
        "Debug.Print &HFF, &O77, 1E3\nEnd Function\n",
    ))
    assert names(codebase.function("vba:A:Run")) == ["LIMIT"]


def test_local_module_variables_and_enum_members_shadow_foreign_constants() -> None:
    codebase = analyze(
        SourceFile("Settings.bas", "Public Const LIMIT = 1\nPublic Const VALUE = 2\n"),
        SourceFile("Use.bas", "Private VALUE As Long\nPrivate Enum Choices\n"
                   "LIMIT = 3\nEnd Enum\n"
                   "Sub Run()\nDebug.Print LIMIT, VALUE\nEnd Sub\n"),
    )
    assert codebase.function("vba:Use:Run").constants == ()


def test_unsupported_local_constant_blocks_foreign_fallback_with_function_diagnostic() -> None:
    codebase = analyze(
        SourceFile("Settings.bas", "Public Const LIMIT = 1\n"),
        SourceFile("Use.bas", "Private Const LIMIT\nSub Run()\n"
                   "Debug.Print LIMIT, Use.LIMIT\nEnd Sub\n"),
    )
    function = codebase.function("vba:Use:Run")
    assert function.constants == ()
    diagnostics = [
        item for item in codebase.diagnostics if item.function_id == function.id
    ]
    assert [item.code for item in diagnostics] == [
        "unresolved_constant", "unresolved_constant",
    ]
