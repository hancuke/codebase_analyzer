# CodeGraph Usage Guide

CodeGraph is a language-independent static analysis library. It accepts complete source
files and language analyzers, then exposes source files, functions, calls, dependencies,
entry points, and diagnostics.

It does not read directories, execute code, call an LLM, or generate prompts. File
discovery and downstream reporting remain the caller's responsibility.

## Analyze a codebase

```python
from codegraph import Codebase, SourceFile, VbaAnalyzer

codebase = Codebase.analyze(
    files=[
        SourceFile(source_id="frmOrder.frm", content="""
Private Sub bSave_Click()
    If ValidateOrder() Then
        Call SaveOrder
    End If
End Sub
""".lstrip()),
        SourceFile(source_id="modOrder.bas", content="""
Public Function ValidateOrder() As Boolean
    ValidateOrder = LoadCustomer()
End Function

Public Sub SaveOrder()
End Sub

Public Function LoadCustomer() As Boolean
End Function
""".lstrip()),
    ],
    analyzers=[VbaAnalyzer()],
)
```

Each file must be supported by exactly one analyzer. Unsupported files, ambiguous
analyzer ownership, duplicate source identifiers, parse problems, and unresolved calls are reported
through `codebase.diagnostics`. Reliable results from other files remain available.

## Reuse VBA syntax analysis

Use the same analyzer instance for related snapshots or batches:

```python
analyzer = VbaAnalyzer(cache_size=4096)
baseline = Codebase.analyze(baseline_files, [analyzer])
working = Codebase.analyze(working_files, [analyzer])
analyzer.clear_cache()
```

`cache_size` is a non-negative integer, defaulting to `0` (disabled). It limits each
of two instance-local LRU caches independently: function extraction and raw call
extraction. Limits count entries, not bytes. No cache is shared between analyzers.
Source identity and complete content key function extraction; procedure content and
one-based start line key raw calls. Changed content or line positions cannot reuse
stale facts. Returned lists and raw-call contexts are independent of cached data.

Only syntax facts, including parse diagnostics, are cached. Name resolution,
unresolved-call diagnostics, entry detection, and graph construction run for every
batch, so different symbol scopes remain independent. Reuse may span scans when the
caller retains the analyzer; `clear_cache()` releases retained syntax without
affecting existing immutable `Codebase` snapshots.

## Inspect diagnostics

```python
for diagnostic in codebase.diagnostics:
    print(diagnostic.severity, diagnostic.code, diagnostic.message)
```

Unresolved calls remain available through `calls_from()`, but do not become dependency
graph edges.

## Locate source files

```python
for source_file in codebase.source_files:
    print(source_file.source_id)

module = codebase.source_file("modOrder.bas")
module_functions = codebase.functions_in_file(module.source_id)

bas_files = codebase.find_source_files(extension=".bas")
```

`source_files` is sorted by source identifier. `functions_in_file()` returns complete immutable
`Function` objects sorted by function ID. An analyzed file with no functions returns an
empty tuple. An unknown source identifier raises `SourceFileNotFoundError`.

`find_source_files()` supports `source_id_prefix`, `language`, and `extension` filters.

## Locate functions

```python
function = codebase.function("vba:frmOrder:bSave_Click")
matching = codebase.find_functions(name="SaveOrder", language="vba")
at_line = codebase.functions_at("modOrder.bas", 5)
```

Function IDs are stable references and do not include line numbers. `find_functions()`
can filter by name, qualified name, file, language, module, or an attribute key/value.
`functions_at()` returns every function containing the requested one-based source line.

Unknown IDs raise `FunctionNotFoundError`; use `get_function()` when a nullable lookup is
more convenient.

## Include directly referenced VBA constants

Batch analysis attaches the module-level constants directly referenced by each
function. The original `source` and its line range are unchanged:

```python
constants_demo = Codebase.analyze(
    [SourceFile("Settings.bas", '''
Private Const PREFIX As String = "Order"
Public Const RETRIES As Long = 3
Private Const UNUSED As Long = 99
Public Sub Save()
    Debug.Print PREFIX, RETRIES
End Sub
'''.lstrip())],
    [VbaAnalyzer()],
)
function = constants_demo.function("vba:Settings:Save")
assert [item.name for item in function.constants] == ["PREFIX", "RETRIES"]
# The caller owns context formatting and sending it to an LLM.
declarations = dict.fromkeys(constant.source for constant in function.constants)
code_for_llm = "\n\n".join((*declarations, function.source))
```

`constants` is an immutable tuple of frozen `Constant` values containing the name,
module, source identifier, original declaration, inclusive line range, visibility,
optional type, and expression text. Unused constants are not exposed. References
are case-insensitive and respect parameters and declared local-variable/constant
shadowing. Public constants in other standard modules can be referenced with or
without their module qualifier; Private and unqualified module-level Const
declarations remain local. Object members are not treated as module constants.

Standard modules are recognized as `.bas` or `Module_*.txt`; instance modules as
`.cls`, `.frm`, `Form_*.txt`, or class export metadata. `.txt` inputs still need an
explicit `language="vba"` hint. Other module kinds do not acquire assumed global
visibility: unresolved module kinds, ambiguous names, and unsupported related
declarations produce diagnostics rather than guessed associations. Conditional
Const declarations are not evaluated.

The library does not assemble supplemental source text. Callers format
`function.constants` alongside `function.source` and choose whether to send the
result to an LLM. Expressions are not evaluated and constants referenced only by
other constants are not expanded. A multi-name declaration is preserved whole;
several constants can share the same `source` and declaration range. Callers can
deduplicate by source identifier and range when combining multiple functions.
Combined declarations are reading material, not necessarily a compilable module.

Existing prompts do not change. For a call-chain context, callers can inspect
`constants` on each selected function.
`ContextLimits.max_source_chars` still counts only original function source, not
these supplemental declarations. Const-only changes do not automatically trigger
the current function-source-based change-impact analysis.

## Inspect calls and dependencies

```python
calls = codebase.calls_from(function.id)
resolved = codebase.calls_from(function.id, resolution="resolved")
unresolved = codebase.calls_from(function.id, resolution="unresolved")
incoming = codebase.calls_to("vba:modOrder:ValidateOrder")

direct = codebase.callees(function.id)
all_dependencies = codebase.callees(function.id, transitive=True)
callers = codebase.callers(function.id, transitive=True)
```

`Call` records the source function, original call name, source line, resolved target
when known, and resolution evidence. Cycles are handled safely and never return the
starting function as its own dependency.

## Inspect entry points and dependency context

Language analyzers identify structural entry points. If the rule produces an unsuitable
result, change the analyzer rule rather than maintaining a second confirmation state:

```python
context = codebase.dependency_context("vba:frmOrder:bSave_Click")
for function in context.functions:
    print(function.id, function.source)
```

Use `ContextLimits` to bound context size:

```python
from codegraph import ContextLimits

context = codebase.dependency_context(
    "vba:frmOrder:bSave_Click",
    limits=ContextLimits(
        max_depth=8,
        max_functions=80,
        max_source_chars=30_000,
    ),
)
if context.truncated:
    print(context.truncation_reasons)
```

## Add a language analyzer

Implement `LanguageAnalyzer`, or inherit from `BaseAnalyzer` and provide language-specific
function, call, and entry-point extraction. See [`analyzer.md`](analyzer.md).
