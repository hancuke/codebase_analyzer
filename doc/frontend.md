# Writing a CodeGraph Language Frontend

A language frontend converts complete source files into CodeGraph facts: functions, calls,
entry candidates, and diagnostics. The frontend understands language syntax; `Codebase`
validates, indexes, and queries the results.

## Recommended approach

Inherit from `BaseFrontend`:

```python
from codegraph import BaseFrontend, Diagnostic, Function, RawCall, SourceFile


class PythonFrontend(BaseFrontend):
    language_name = "python"
    file_extensions = {".py"}
    is_case_sensitive = True

    def extract_functions(
        self, source_file: SourceFile
    ) -> tuple[list[Function], list[Diagnostic]]:
        return functions, diagnostics

    def extract_raw_calls(self, function: Function) -> list[RawCall]:
        return [RawCall(name="calculate_total", line=15)]
```

The base class handles frontend batching, symbol indexing, call resolution, unresolved-call
diagnostics, and `FileAnalysis` construction.

## Frontend protocol

For a custom pipeline, implement:

```python
class LanguageFrontend(Protocol):
    def supports(self, file: SourceFile) -> bool: ...
    def analyze(self, files: Sequence[SourceFile]) -> FileAnalysis: ...
```

The input contains complete files already assigned to the frontend. The file order must
not change the result. A frontend may analyze multiple files together for same-language
cross-file and forward references.

## Required frontend behavior

### File ownership

`supports()` should honor an explicit `SourceFile.language` first and use the extension as
a fallback. Do not claim every unknown extension, and do not inspect source content with
complex heuristics to compete with another frontend.

### Function extraction

Each `Function` must have:

1. a stable ID independent of line numbers;
2. a unique language/module namespace;
3. the original source text;
4. a one-based inclusive source range;
5. the input file path in `Function.file`.

Language-specific case rules, overloads, classes, packages, and nested functions belong in
the frontend's IDs and resolution logic.

### Call extraction

Return one `RawCall` per discovered call site. Resolve a target only when language
semantics make the target reliable. For ambiguous, dynamic, reflective, or unsupported
calls, return no target and emit a diagnostic rather than guessing.

### Entry candidates

`detect_entry_candidate()` may identify language-level hints such as form events, `main`
functions, tests, scheduled jobs, or controllers. The caller must explicitly accept or
replace the entry set.

## BaseFrontend hooks

| Hook | Purpose |
| --- | --- |
| `language_name` | Stable language identifier. |
| `file_extensions` | Extension fallback set. |
| `is_case_sensitive` | Name and extension normalization behavior. |
| `extract_functions(file)` | Return functions and file diagnostics. |
| `extract_raw_calls(function)` | Return unresolved call sites. |
| `detect_entry_candidate(function)` | Optionally return an entry hint. |
| `resolve_target(...)` | Override name resolution for language semantics. |

## Failure and determinism rules

- Preserve valid results when only part of a file is unsupported.
- Report syntax and resolution issues as structured diagnostics.
- Never silently convert parser failure into an empty successful analysis.
- Keep function IDs, ranges, calls, diagnostics, and ordering deterministic.
- Do not access `Codebase` internals, the filesystem, or external services.

See [`usage.md`](usage.md) for caller workflows and [`spec.md`](spec.md) for the public
contract.
