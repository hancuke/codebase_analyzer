# Copilot instructions for the CodeGraph workspace

## Project overview

This repository is a `uv` workspace containing four packages:

- `code_graph/`: language-independent static analysis and dependency queries.
- `file_tracker/`: immutable physical file changes and baseline transactions.
- `change_analyzer/`: old/new CodeGraph comparison and entry-impact analysis.
- `document_updater/`: document authoring, entry-oriented plans, and Markdown fragment updates.

The main flow is:

```text
SourceFile[] + LanguageAnalyzer[]
    -> Codebase.analyze()
    -> analyzer batches
    -> AnalysisResult facts
    -> core validation and indexes
    -> deterministic Codebase queries
    -> optional bounded dependency_context()
```

`Codebase` owns routing, aggregation, validation, indexing, graph traversal, and public
queries. An analyzer owns syntax parsing, language-specific name resolution, call
extraction, entry-point rules, and language diagnostics. Analyzers may receive a batch of
same-language files so forward and cross-file references can be resolved.

The stable CodeGraph model is in `code_graph/src/codegraph/model.py`; the facade and
graph algorithms are in `code_graph/src/codegraph/core.py`; analyzer extension points
are in `code_graph/src/codegraph/analyzer.py`; language implementations are in
`code_graph/src/codegraph/vba.py` and `code_graph/src/codegraph/oracle.py`.

## Build, test, and lint commands

Dependencies are managed with `uv` and the project requires Python 3.10 or newer.

```bash
# Install/synchronize the development environment
uv sync --all-packages

# Run the complete test suite
uv run pytest code_graph/tests file_tracker/tests change_analyzer/tests document_updater/tests

# Run one test
uv run pytest code_graph/tests/test_codebase.py::test_vba_analysis_resolves_cross_file_and_forward_calls

# Run one test module
uv run pytest change_analyzer/tests/test_change_analyzer.py

# Run the end-to-end MVP example
uv run python examples/entry_document_update.py

# Build all workspace packages
uv build --all-packages
```

There is no configured lint command or lint tool.

## Repository-specific conventions

- Use `Codebase.analyze(files, analyzers)` as the supported construction path. Direct
  `Codebase` construction bypasses validation and is intentionally guarded.
- `SourceFile.source_id` is a logical, canonical project-relative POSIX identifier:
  use `/`, no leading slash, drive letter, workspace root, `.` or `..` segments. It is
  not a filesystem `Path`; adapters own physical-path mapping.
- Source text is complete input, not a diff. `Function.id` must be stable and unique
  without depending on line numbers or character offsets.
- Domain objects are frozen dataclasses. Preserve immutable mappings/tuples and do not
  introduce mutable state into the analysis snapshot.
- Every input file must be supported by exactly one analyzer. Zero or multiple matches
  become `unsupported_file` or `ambiguous_analyzer` diagnostics.
- Collect all functions before resolving calls. Only reliable calls with a valid
  `target_id` become graph edges; unresolved calls remain visible through `calls_from()`
  and must have structured diagnostics rather than being silently discarded.
- Expected parse, routing, and resolution problems are `Diagnostic` values. Unknown
  source/function lookups use `SourceFileNotFoundError` or `FunctionNotFoundError`.
  Unexpected implementation failures should propagate.
- Preserve deterministic ordering of functions, calls, entries, diagnostics, and query
  results. Dependency traversal must handle cycles without returning the starting
  function as its own dependency.
- Keep language-specific behavior inside the corresponding analyzer. New analyzers
  should normally inherit `BaseAnalyzer`, define `language_name` and
  `file_extensions`, and implement `extract_functions()` and
  `extract_raw_calls()`; override resolution or entry detection only for language rules.
- Analyzer `supports()` should honor an explicit `SourceFile.language` hint before
  falling back to the extension. Do not claim unknown files with broad catch-all
  ownership.
- Use one-based inclusive `SourceRange` line numbers. Keep function source, source IDs,
  call locations, and diagnostic locations traceable back to the original snapshot.
- Do not add filesystem traversal, Git/change tracking, cross-language linking, CLI/UI,
  serialization, AST exposure, execution, or AI/RAG orchestration to the core package.
  Put those concerns in caller adapters.
- Keep workspace responsibilities one-directional: `change_analyzer` may compose
  FileTracker and CodeGraph; `document_updater` may consume change-analysis results.
  Do not make either core package depend on these orchestration packages.
- Entry impact is computed from the union of reverse reachability in the baseline and
  working graphs. This is required to preserve deleted dependencies and discover new ones.
- A changed function may affect multiple entries. Keep the relationship many-to-many,
  retain old/new path evidence, and preserve changes with no reachable entry as
  `unassigned_changes`.
- Documentation planning is deterministic. LLM calls receive one entry plan at a time;
  they do not infer entry ownership from a repository-wide diff.

## Tests and examples

Tests live under each workspace package and cover public behavior and analyzer pipelines.
When changing a public model, routing rule, graph query, or analyzer contract, update
the relevant tests and examples together. `examples/entry_document_update.py`
demonstrates the end-to-end incremental documentation MVP.
