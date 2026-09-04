# CodeGraph Specification

## Scope

CodeGraph is a static-analysis core for third-party callers. It accepts complete source
files and language analyzers, discovers functions and calls, and exposes reliable
single-language dependency queries.

The core does not scan directories, read from disk, execute code, or link calls across
languages.

The public API inventory and modularity decisions are maintained in
[`api-boundary.md`](api-boundary.md).

## Input contract

```python
SourceFile(
    source_id="modOrder.bas",
    content=source_text,
    language="vba",  # optional
)
```

`source_id` is a `str` used as a stable workspace-relative source identifier. The
caller must provide it in canonical project-relative POSIX form: `/` separators, no
leading slash, drive letter, workspace root, `.` segment, or `..` segment. For example,
`src/order.bas` identifies `/workspace/orders/src/order.bas` when the caller's workspace
root is `/workspace/orders`. The core returns the same identifier in all source
references and does not convert it to `pathlib.Path`, make it absolute, resolve
symlinks, read it, or compare filesystem identities. Equal canonical identifier strings
are duplicates. `content` is complete source text, not a diff. `language` is an optional
`str` hint that may override extension-based analyzer routing.

Core object attributes, types, invariants, and the flow from `SourceFile` through
`FileAnalysis` to `Codebase` and `AnalysisContext` are defined in
[`api-boundary.md`](api-boundary.md).

Every file must have exactly one supporting analyzer. Zero supporters produce
`unsupported_file`; multiple supporters produce `ambiguous_frontend`.

## Domain models

### `Function`

An analyzer must provide:

- a stable, codebase-unique `id`;
- `name`, `language`, `module`, and source `file`;
- the original function `source`;
- a one-based inclusive `SourceRange`;
- optional serializable `attributes`.

IDs must not depend on line numbers or character offsets.

### `Call`

Each discovered call site is represented by:

- `source_id`;
- original `name`;
- one-based source `line`;
- `target_id` only when resolution is reliable;
- optional resolution `evidence`.

Unresolved calls are retained and diagnosed, but never become graph edges.

### `Diagnostic`

Diagnostics contain a stable `code`, `severity`, human-readable `message`, and optional
file, function, and line location. Expected parser and resolution problems are reported;
unexpected programming errors must not be converted into successful empty results.

## Public queries

`Codebase` provides:

- `source_files`, `source_file()`, `find_source_files()`;
- `functions`, `function()`, `get_function()`, `functions_in_file()`;
  `find_functions()`, `functions_at()`;
- `calls_from()`, `calls_to()`;
- `callees()`, `callers()`, and their transitive variants;
- entry candidate acceptance, entry marking, replacement, removal, and enumeration;
- bounded `dependency_context()`;

Queries are read-only except for explicit entry management. Results
are immutable and deterministically ordered.

## Context limits

`ContextLimits` may constrain maximum graph depth, function count, and source characters.
`Codebase.dependency_context(function_id, limits=...)` accepts any indexed function as
the traversal root; it does not require the function to be an `EntryPoint`. When content
is omitted because a limit is reached, `AnalysisContext.truncated` is true and
`truncation_reasons` identifies the active limits.

## Language analyzer contract

```python
class LanguageAnalyzer(Protocol):
    def supports(self, file: SourceFile) -> bool: ...
    def analyze(self, files: Sequence[SourceFile]) -> AnalysisResult: ...
```

Language analyzers may batch files from one language to resolve forward and cross-file
references. They must return `AnalysisResult` containing functions, calls, entry points,
and diagnostics. Language-specific rules must remain in the analyzer. If entry-point
classification is unsuitable, change the analyzer rule rather than adding a second
confirmation workflow to the core.

## Non-goals

- directory traversal and project discovery;
- source change tracking and impact analysis;
- code execution or compilation;
- guaranteed resolution of dynamic dispatch, reflection, macros, or runtime loading;
- automatic business-entry selection;
- cross-language dependency linking;
- prompt, report, or visualization generation.
