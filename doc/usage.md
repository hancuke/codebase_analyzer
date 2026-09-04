# CodeGraph Usage Guide

CodeGraph is a language-independent static analysis library. It accepts complete source
files and language frontends, then exposes source files, functions, calls, dependencies,
entry points, and diagnostics.

It does not read directories, execute code, call an LLM, or generate prompts. File
discovery and downstream reporting remain the caller's responsibility.

## Analyze a codebase

```python
from codegraph import Codebase, SourceFile, VbaFrontend

codebase = Codebase.analyze(
    files=[
        SourceFile("frmOrder.frm", """
Private Sub bSave_Click()
    If ValidateOrder() Then
        Call SaveOrder
    End If
End Sub
""".lstrip()),
        SourceFile("modOrder.bas", """
Public Function ValidateOrder() As Boolean
    ValidateOrder = LoadCustomer()
End Function

Public Sub SaveOrder()
End Sub

Public Function LoadCustomer() As Boolean
End Function
""".lstrip()),
    ],
    frontends=[VbaFrontend()],
)
```

Each file must be supported by exactly one frontend. Unsupported files, ambiguous
frontend ownership, duplicate paths, parse problems, and unresolved calls are reported
through `codebase.diagnostics`. Reliable results from other files remain available.

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
    print(source_file.path)

module = codebase.source_file("modOrder.bas")
module_functions = codebase.functions_in_file(module.path)

bas_files = codebase.find_source_files(extension=".bas")
```

`source_files` is sorted by path. `functions_in_file()` returns complete immutable
`Function` objects sorted by function ID. An analyzed file with no functions returns an
empty tuple. An unknown path raises `SourceFileNotFoundError`.

`find_source_files()` supports `path_prefix`, `language`, and `extension` filters.

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

## Manage entry points and context

Frontends may suggest entry points, but callers decide which suggestions are business
entries:

```python
codebase.accept_entry_candidates()
codebase.mark_entries(
    lambda function: function.attributes.get("visibility") == "public",
    kind="public_procedure",
)
codebase.set_entries(["vba:frmOrder:bSave_Click"], kind="form_event")

context = codebase.context_for("vba:frmOrder:bSave_Click")
for function in context.functions:
    print(function.id, function.source)
```

Entries can be removed with `remove_entries(function_ids, kind=...)`.

Use `ContextLimits` to bound context size:

```python
from codegraph import ContextLimits

context = codebase.context_for(
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

## Add a language frontend

Implement `LanguageFrontend`, or inherit from `BaseFrontend` and provide language-specific
function and call extraction. See [`frontend.md`](frontend.md).
