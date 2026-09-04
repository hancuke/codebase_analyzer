# API Simplification Implementation Plan

This document translates [`api-boundary.md`](api-boundary.md) into independently
reviewable implementation steps. It is the implementation checklist for the current
API redesign; code changes must follow this order and must not mix unrelated steps.

## Target public model

The core exposes one dependency snapshot built from complete source inputs:

```text
SourceFile[] + LanguageAnalyzer[]
    -> AnalysisResult
    -> Codebase
    -> dependency queries
    -> dependency_context(function_id, limits)
```

The core does not expose filesystem behavior, source-change tracking, business-entry
confirmation state, or UI/frontend concepts.

## Migration principles

1. Change one responsibility group at a time.
2. Keep the dependency graph algorithm unchanged unless a step explicitly requires it.
3. Update implementation, tests, examples, and public documentation in the same step.
4. Remove obsolete APIs rather than keeping aliases unless compatibility is explicitly
   approved.
5. Preserve deterministic ordering, immutable result values, diagnostics, and lookup
   exceptions.
6. Do not introduce a compatibility layer that creates a second vocabulary or a second
   source of truth.

## Step 1: Rename source identity to `source_id`

### Scope

Replace file-system-looking API names with the logical source identifier:

| Current | Target |
| --- | --- |
| `SourceFile.path` | `SourceFile.source_id` |
| `Function.file` | `Function.source_id` |
| `Diagnostic.path` | `Diagnostic.source_id` |
| `find_source_files(path_prefix=...)` | `find_source_files(source_id_prefix=...)` |
| `source_file(path)` | `source_file(source_id)` |
| `functions_in_file(path)` | `functions_in_file(source_id)` |
| `functions_at(path, line)` | `functions_at(source_id, line)` |

### Invariants

- The value type is `str`.
- The identifier is project-relative, canonical, and uses `/`.
- The core does not read, normalize, absolutize, or resolve the identifier.
- The same identifier is used in `SourceFile`, `Function`, and `Diagnostic`.
- Physical-path mapping remains in the caller adapter.

### Acceptance criteria

- No implementation or documentation reference uses the old public field names.
- Existing dependency resolution behavior is unchanged.
- Tests cover duplicate identifiers, source lookup, function ownership, and diagnostics.

## Step 2: Rename bounded context export

### Scope

Replace:

```python
codebase.context_for(function_id, limits=limits)
```

with:

```python
codebase.dependency_context(function_id, limits=limits)
```

### Invariants

- Any indexed function ID can be the traversal root.
- The root does not need to be an `EntryPoint`.
- The result remains an `AnalysisContext`.
- Limits and truncation reasons retain their current meaning.
- No context result is fed back into analysis.

### Acceptance criteria

- `context_for()` is absent from the supported API.
- Tests cover direct roots, transitive dependencies, cycles, limits, and truncation.
- Documentation calls this a bounded dependency subgraph, not a business context.

## Step 3: Simplify entry-point semantics

### Scope

Make structural entry points analyzer output rather than caller-managed state:

| Current | Target |
| --- | --- |
| `EntryCandidate` | Remove |
| `Codebase.entry_candidates` | Remove |
| `accept_entry_candidates()` | Remove |
| `mark_entries()` | Remove |
| `set_entries()` | Remove |
| `remove_entries()` | Remove |
| mutable confirmed-entry registry | Remove |
| analyzer entry hook | `detect_entry_point()` |

`Codebase.entry_points` remains an immutable view of analyzer-identified structural
entries. If the classification is unsuitable, the analyzer rule is changed.

### Invariants

- An analyzer may emit zero or more `EntryPoint` values.
- The core validates entry function IDs before exposing them.
- Entry points are facts from the same analysis snapshot as functions and calls.
- Entry points do not control `dependency_context()`.

### Acceptance criteria

- No caller confirmation workflow remains.
- Entry points cannot become detached from indexed functions.
- Tests cover analyzer-provided entries and invalid entry references.

## Step 4: Clarify analyzer terminology

### Scope

Use language-analysis terminology in the design and target API:

| Current | Target |
| --- | --- |
| `LanguageFrontend` | `LanguageAnalyzer` |
| `BaseFrontend` | `BaseAnalyzer` |
| `FileAnalysis` | `AnalysisResult` |
| `frontend.py` | `analyzer.py` |
| `VbaFrontend` | `VbaAnalyzer` |
| `OraclePlsqlFrontend` | `OraclePlsqlAnalyzer` |

The old names are not retained as aliases unless a separate compatibility decision is
made before implementation.

### Invariants

- The analyzer owns syntax, language-specific resolution, and entry-point rules.
- The core owns routing, aggregation, validation, indexing, and graph queries.
- An analyzer never mutates `Codebase` or calls another analyzer.

### Acceptance criteria

- Public exports use one vocabulary.
- Documentation and examples use analyzer terminology.
- No UI/frontend interpretation is required to understand the extension point.

## Step 5: Restrict construction and validate facts

### Scope

- Make `Codebase.analyze()` the only supported construction path.
- Keep direct construction internal.
- Validate functions, calls, and entry points during aggregation.
- Convert expected malformed analyzer facts into `Diagnostic` values.

### Invariants

- A constructed snapshot has internally consistent indexes.
- `Codebase` callers cannot inject partially indexed state.
- Unexpected implementation errors still propagate.

### Acceptance criteria

- Direct construction is not part of the documented public API.
- Invalid analyzer references are diagnosed before indexing.
- Existing lookup and traversal behavior remains deterministic.

## Step 6: Reassess internal decomposition

This step is conditional and must not precede the public API migration. Only extract
internal components when a concrete responsibility cannot remain local to `Codebase`.
Candidate internal components are:

- analyzer routing and result aggregation;
- fact validation;
- dependency index construction and traversal.

No internal component should become a new public abstraction without a separate design
decision.

## Validation matrix

Each implementation step must run the smallest existing validation that covers its
scope:

| Area | Required validation |
| --- | --- |
| Model rename | Model construction and field assertions |
| Source queries | Lookup, filtering, ownership, and diagnostics tests |
| Dependency queries | Direct, transitive, reverse, and cycle tests |
| Context export | Limit and truncation tests |
| Analyzer contract | Custom analyzer pipeline tests |
| Public boundary | Import/export and obsolete-name absence checks |

The full test suite is required before declaring all steps complete. If the environment
cannot run it, the limitation must be reported rather than treating a partial check as
completion.

## Completion condition

The redesign is complete only when the implementation, tests, examples, and all
design/specification documents agree on one public vocabulary and one source of truth
for each responsibility.
