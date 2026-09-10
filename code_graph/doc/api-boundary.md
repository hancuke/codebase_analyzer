# API Boundary and Modularity Agreement

This document is the design checkpoint for the public API. It separates interfaces
needed by callers from interfaces needed only by language analyzer authors, and keeps
implementation details out of the stable boundary.

## Design objective

CodeGraph has one primary responsibility:

> Build a dependency graph from caller-provided source files and expose reliable
> dependency facts and queries.

The library does not discover projects, track source changes, select business entries
automatically, execute code, or produce reports.

The complexity is split into these local problems:

| Local problem | Owning interface | Output |
| --- | --- | --- |
| Represent caller input and analysis facts | Immutable model objects | `SourceFile`, `Function`, `Call`, and diagnostics |
| Parse, resolve, and identify entries in one language | `LanguageAnalyzer` / `BaseAnalyzer` | `AnalysisResult` |
| Aggregate multiple analyzer results | `Codebase.analyze()` | One validated snapshot |
| Query files, symbols, calls, and dependencies | `Codebase` read APIs | Deterministic immutable results |
| Select and export dependency context | `Codebase` context APIs | `EntryPoint` and `AnalysisContext` |

No local problem needs to know about filesystem access, Git, change impact, or report
formatting.

## Value and source identifier conventions

The model layer is the contract between the caller, the core, and analyzers. Every
attribute below is part of that contract; an implementation must not silently change
its type or interpretation.

| Object | Attribute | Type | Meaning and invariant |
| --- | --- | --- | --- |
| `SourceFile` | `source_id` | `str` | Stable workspace-relative source identifier. It is a logical locator, not a `Path`; callers must provide it in canonical project-relative POSIX form. |
| `SourceFile` | `content` | `str` | Complete source text for the file, not a partial diff. |
| `SourceFile` | `language` | `str \| None` | Optional language hint. When present, analyzer routing uses it instead of the file extension. |
| `ContextLimits` | `max_depth` | `int \| None` | Maximum dependency-edge depth; `None` means unbounded and non-negative values are valid. |
| `ContextLimits` | `max_functions` | `int \| None` | Maximum number of functions in a context; `None` means unbounded and positive values are valid. |
| `ContextLimits` | `max_source_chars` | `int \| None` | Maximum selected source characters; `None` means unbounded and non-negative values are valid. |
| `SourceRange` | `start_line`, `end_line` | `int` | One-based inclusive source lines. `start_line <= end_line`. |
| `Function` | `id` | `str` | Stable codebase-unique function identifier. It must not depend on line numbers or offsets. |
| `Function` | `name`, `language`, `module`, `source_id`, `source` | `str` | Function identity, language, owning module, source identifier, and complete function source. `source_id` uses the same convention as `SourceFile.source_id`. |
| `Function` | `source_range` | `SourceRange` | Location of the complete function in `source_id`. |
| `Function` | `attributes` | `Mapping[str, object]` | Optional language-specific descriptive metadata. The exposed mapping is immutable; it is not an extension mechanism for core behavior. |
| `Call` | `source_id`, `name` | `str` | Calling function ID and original source-level call name. |
| `Call` | `line` | `int` | One-based source line in the calling function's file. |
| `Call` | `target_id`, `evidence` | `str \| None` | Reliable resolved target and optional explanation of the resolution. A missing target means the call remains a fact but is not a graph edge. |
| `Diagnostic` | `code`, `severity`, `message` | `str` | Stable machine-readable category, severity, and human-readable explanation. |
| `Diagnostic` | `source_id`, `function_id` | `str \| None` | Optional location references using the same source identifier and function ID conventions. |
| `Diagnostic` | `line` | `int \| None` | Optional one-based source line. |

For the default traceability contract, `source_id` is a canonical project-relative
source location:

- use `/` as the separator, regardless of the host operating system;
- do not use a leading `/`, drive letter, `.` segment, or `..` segment;
- do not include the workspace root;
- preserve the source file's meaningful case; callers must use one case policy per
  project;
- use the same identifier everywhere a source is referenced.

For example, an adapter may read `/workspace/orders/src/order.bas` and pass
`source_id="src/order.bas"`. The core returns that same identifier in
`SourceFile.source_id`, `Function.source_id`, and `Diagnostic.source_id`. The adapter
can reconstruct or look up the physical path using its workspace-root mapping:

```text
source_id:    src/order.bas
workspace:    /workspace/orders
physical file: /workspace/orders/src/order.bas
```

The core does not resolve symlinks, convert identifiers to absolute paths, read the
identifier, or compare filesystem identities. Duplicate detection applies to equal
canonical identifier strings, not to equivalent filesystem locations. A caller that
combines multiple projects must namespace source identifiers before analysis if their
relative names can collide.

If a caller needs to preserve a physical path that is different from the traceability
identifier, that mapping belongs in the caller adapter. It must not be overloaded into
`source_id`; a future optional metadata field may carry such provenance without
changing the core identity contract.

## Object flow

The object flow is intentionally one-directional:

```text
caller
  -> SourceFile[] + LanguageAnalyzer[]
  -> Codebase.analyze()
  -> analyzer routing
  -> analyzer batch analysis
  -> AnalysisResult
  -> core validation and aggregation
  -> indexed Codebase snapshot
  -> query results: Function[], Call[], Diagnostic[]
  -> dependency_context(function_id, limits)
  -> bounded AnalysisContext
```

The lifecycle rules are:

1. The caller creates complete immutable `SourceFile` values and supplies analyzer
   instances.
2. `Codebase.analyze()` deduplicates source identifier strings, routes each file to exactly one
   analyzer, and sends each analyzer its supported batch.
3. An analyzer converts source text into `Function`, `Call`, `EntryPoint`, and
   `Diagnostic` values. It owns syntax and language-specific resolution, but does not
   mutate the core or other analyzers.
4. The core validates and aggregates analyzer facts, then builds forward and reverse
   dependency indexes. Only calls with a valid `target_id` become graph edges.
5. The resulting `Codebase` is one analysis snapshot. Query methods do not mutate that
   snapshot or entry facts.
6. `dependency_context()` derives an `AnalysisContext` from the snapshot and supplied
   `ContextLimits`; it accepts any indexed function ID and does not require an
   `EntryPoint` or feed results back into analysis.

`AnalysisResult` is an extension-boundary transport object, not a second mutable
codebase. `AnalysisContext` is a derived view, not a new source of truth. This prevents
callers from confusing raw analyzer facts, indexed graph state, and bounded output.

## Public caller API

These interfaces are required by callers integrating CodeGraph.

| Interface | Required? | Responsibility | Boundary rule |
| --- | --- | --- | --- |
| `Codebase.analyze(files, analyzers)` | Yes | Build one dependency snapshot from complete source files | The only supported construction path |
| `Codebase.source_files` | Yes | Enumerate the input snapshot | Returns deterministic immutable results |
| `Codebase.source_file(source_id)` | Yes | Retrieve one known source file | Raises `SourceFileNotFoundError` for an unknown source identifier |
| `Codebase.find_source_files(...)` | Yes | Filter source files by source identifier, language, or extension | Filtering only; no discovery or I/O |
| `Codebase.functions` | Yes | Enumerate indexed functions | Returns stable function objects |
| `Codebase.function(id)` | Yes | Required function lookup | Raises `FunctionNotFoundError` for an unknown ID |
| `Codebase.get_function(id)` | Yes | Nullable function lookup | Use when absence is an expected branch |
| `Codebase.functions_in_file(source_id)` | Yes | Enumerate functions in one source | Unknown source identifiers remain errors |
| `Codebase.find_functions(...)` | Yes | Filter functions by identity and metadata | Does not perform fuzzy or cross-language resolution |
| `Codebase.functions_at(source_id, line)` | Yes | Map a source location to functions | Uses one-based inclusive source ranges |
| `Codebase.calls_from(id, resolution=...)` | Yes | Expose outgoing call-site facts | Keeps unresolved calls visible |
| `Codebase.calls_to(id)` | Yes | Expose incoming call-site facts | Only resolved targets are returned |
| `Codebase.callees(id, transitive=...)` | Yes | Query direct or transitive dependencies | Traverses reliable graph edges only |
| `Codebase.callers(id, transitive=...)` | Yes | Query direct or transitive dependents | Traverses reliable reverse edges only |
| `Codebase.dependencies_of(id)` | No, alias | Older vocabulary for direct callees | Candidate for removal; use `callees()` |
| `Codebase.dependents_of(id)` | No, alias | Older vocabulary for direct callers | Candidate for removal; use `callers()` |
| `Codebase.descendants_of(id)` | No, alias | Older vocabulary for transitive callees | Candidate for removal; use `callees(..., transitive=True)` |
| `Codebase.ancestors_of(id)` | No, alias | Older vocabulary for transitive callers | Candidate for removal; use `callers(..., transitive=True)` |
| `Codebase.diagnostics` | Yes | Report routing, parsing, and resolution problems | Diagnostics are facts, not exceptions for expected input problems |
| `Codebase.entry_points` | Yes | Enumerate analyzer-identified structural entries | If classification is wrong, change the analyzer rule |
| `Codebase.dependency_context(function_id, limits=...)` | Yes | Export a bounded dependency subgraph from any function | Context is derived from reliable dependency edges |

`Codebase.__init__()` is not a caller interface even though it is technically public in
Python. It accepts already-aggregated internal structures and can bypass validation.
The implementation must treat it as an internal construction boundary; future changes
should make `analyze()` the only supported construction path.

## Public domain models

These immutable value objects are necessary because they are the stable data exchanged
between the core, analyzers, and downstream callers.

| Model | Required? | Responsibility |
| --- | --- | --- |
| `SourceFile` | Yes | Complete source text, stable source identifier, optional language hint |
| `SourceRange` | Yes | One-based inclusive function location |
| `Function` | Yes | Stable identity, source ownership, source text, and attributes |
| `Call` | Yes | Call-site fact and optional reliable target |
| `Diagnostic` | Yes | Structured expected problem with location |
| `ContextLimits` | Yes | Explicit bounds for context generation |
| `AnalysisContext` | Yes | Bounded functions, calls, paths, and diagnostics |
| `EntryPoint` | Yes | Analyzer-identified structural analysis root |
| `AnalysisResult` | Extension-facing | Batch result returned by an analyzer |

`Function.attributes` is intentionally an open mapping because language-specific
metadata differs. It must remain descriptive metadata, not a second hidden control API.

## Language analyzer extension API

Language analyzer authors have a separate interface from normal callers. “Frontend” is
not used as the design term because it can be confused with a user-interface layer.
The analyzer vocabulary is the only supported public terminology.

### Minimal protocol

```python
class LanguageAnalyzer(Protocol):
    def supports(self, file: SourceFile) -> bool: ...
    def analyze(self, files: Sequence[SourceFile]) -> AnalysisResult: ...
```

This is the required extension boundary. The core owns routing and aggregation; the
analyzer owns syntax, language-specific name resolution, and entry-point rules.

### Template base class

`BaseAnalyzer` is a convenience implementation, not a second core abstraction. It
provides:

- `supports()` from `language_name` and `file_extensions`;
- name normalization through `is_case_sensitive`;
- the standard extraction and resolution pipeline;
- optional `detect_entry_point()`;
- optional `resolve_target()`.

Required subclass hooks:

| Hook | Responsibility |
| --- | --- |
| `language_name` | Stable language label |
| `file_extensions` | Extension-based routing |
| `extract_functions(file)` | Extract functions and parser diagnostics |
| `extract_raw_calls(function)` | Extract unresolved call-site facts |
| `detect_entry_point(function)` | Identify a language-specific structural entry point |

The current `resolve_target()` return type combines a target and a failure explanation
through either `str | None` or a tuple. This is an extension contract that is harder to
understand than the rest of the API. Before implementation changes, agree on one
explicit resolution result type; do not add more tuple conventions.

## Interfaces deliberately outside the boundary

The following are not CodeGraph responsibilities and must stay in caller adapters:

- directory traversal and project discovery;
- reading files from disk;
- Git integration, file watching, source change tracking, and impact analysis;
- cross-language dependency linking;
- serialization, visualization, reports, prompts, and AI/RAG orchestration;
- parser AST exposure and code execution.

## Complexity controls

The following rules are part of the design, not implementation suggestions:

1. `Codebase` is the read/query facade for one immutable analysis snapshot.
2. Language analyzers return facts; they do not mutate `Codebase` or call other analyzers.
3. Core validation occurs before facts become indexed graph edges.
4. Expected analysis problems are represented by `Diagnostic`; unknown lookup keys use
   explicit domain exceptions.
5. Every public method must belong to exactly one responsibility group: snapshot,
   lookup, dependency query, entry inspection, or context construction.
6. Aliases must not multiply vocabulary. Keep one canonical name for each operation.
7. Internal indexes, aggregation records, and construction helpers are not public API.

## Decisions required before implementation changes

The next code change is considered well-scoped only if it addresses one of these
decisions without mixing unrelated behavior:

1. **Construction boundary:** hide or restrict direct `Codebase` construction.
2. **Fact validation:** validate analyzer function, call, and entry references in one
   aggregation step.
3. **Vocabulary reduction:** choose canonical dependency names and remove redundant
   aliases.
4. **Resolution result:** replace the overloaded `resolve_target()` return shape with a
   named result contract.
5. **Facade decomposition:** extract internal aggregation/index components only when a
   concrete responsibility cannot remain local to `Codebase`.

Until one decision is selected, no implementation refactor should be started. This
keeps the work decomposed into independently reviewable changes.
