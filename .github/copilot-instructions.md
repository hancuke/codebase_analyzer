# Copilot instructions for CodeGraph

## Project overview

CodeGraph is a small, language-independent static-analysis library. It builds one
immutable dependency snapshot from caller-provided complete source files and language
analyzers. It does not discover files, read from disk, execute code, expose parser ASTs,
link dependencies across languages, or generate reports/prompts.

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

The stable model is in `src/codegraph/model.py`; the facade and graph algorithms are in
`src/codegraph/core.py`; analyzer extension points are in `src/codegraph/analyzer.py`;
language implementations are in `src/codegraph/vba.py` and
`src/codegraph/oracle.py`. The design contract and API decisions are documented in
`doc/architecture.md`, `doc/api-boundary.md`, `doc/spec.md`, and `doc/analyzer.md`.

## Build, test, and lint commands

Dependencies are managed with `uv` and the project requires Python 3.10 or newer.

```bash
# Install/synchronize the development environment
uv sync

# Run the complete test suite
uv run pytest

# Run one test
uv run pytest tests/test_codebase.py::test_vba_analysis_resolves_cross_file_and_forward_calls

# Run one test module
uv run pytest tests/test_oracle.py
```

There is no configured lint command or lint tool in `pyproject.toml`. The project has a
standard setuptools build configuration; use `uv build` when a package artifact needs to
be produced.

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

## Tests and examples

Tests live in `tests/` and cover both public query behavior and analyzer pipelines.
When changing a public model, routing rule, graph query, or analyzer contract, update
the relevant tests and examples together. The executable examples in `scripts/main.py`
and `scripts/pkg.py` demonstrate the VBA and Oracle PL/SQL workflows.

