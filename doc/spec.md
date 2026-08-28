# CodeGraph Specification

## Scope

CodeGraph is a static-analysis core for third-party callers. It accepts complete source
files and language frontends, discovers functions and calls, and exposes reliable
single-language dependency queries.

The core does not scan directories, read from disk, execute code, or link calls across
languages.

## Input contract

```python
SourceFile(
    path="modOrder.bas",
    content=source_text,
    language="vba",  # optional
)
```

Paths are unique within an analysis snapshot. Content is complete source text. A language
hint may override extension-based frontend routing.

Every file must have exactly one supporting frontend. Zero supporters produce
`unsupported_file`; multiple supporters produce `ambiguous_frontend`.

## Domain models

### `Function`

A frontend must provide:

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
- bounded `context_for()`;
- `refresh()` and change-impact results.

Queries are read-only except for explicit entry management and refresh operations. Results
are immutable and deterministically ordered.

## Context limits

`ContextLimits` may constrain maximum graph depth, function count, and source characters.
When content is omitted because a limit is reached, `AnalysisContext.truncated` is true
and `truncation_reasons` identifies the active limits.

## Frontend contract

```python
class LanguageFrontend(Protocol):
    def supports(self, file: SourceFile) -> bool: ...
    def analyze(self, files: Sequence[SourceFile]) -> FileAnalysis: ...
```

Frontends may batch files from one language to resolve forward and cross-file references.
They must return `FileAnalysis` containing functions, calls, entry candidates, and
diagnostics. Language-specific rules must remain in the frontend.

## Refresh contract

`refresh()` replaces changed paths, removes requested paths, rebuilds the current set of
files, and compares old and new functions and graph edges. It must report affected
confirmed entries even when a function or call was removed.

## Non-goals

- directory traversal and project discovery;
- code execution or compilation;
- guaranteed resolution of dynamic dispatch, reflection, macros, or runtime loading;
- automatic business-entry selection;
- cross-language dependency linking;
- prompt, report, or visualization generation.
