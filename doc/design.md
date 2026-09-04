# CodeGraph Caller-Oriented Design

## Purpose

CodeGraph answers a practical static-analysis question:

> Which code is reachable from a selected function?

The caller supplies complete source files and one or more language frontends. CodeGraph
returns traceable facts and composable queries. It does not expose ASTs, graph indexes,
filesystem access, prompt construction, or LLM integrations.

An analysis may contain multiple language frontends, but the core only creates reliable
relationships within each language. Cross-language linking is outside the public contract.

## Core objects

| Object | Purpose |
| --- | --- |
| `SourceFile` | Complete source text, path, and optional language hint. |
| `Function` | Stable ID, name, module, file, source text, range, and attributes. |
| `Call` | A call site with source, location, target if reliably resolved, and evidence. |
| `Diagnostic` | Machine-readable analysis issue with severity and source location. |
| `EntryPoint` | Caller-confirmed business starting point. |
| `AnalysisContext` | Bounded reachable functions, calls, paths, and diagnostics. |

Function IDs are persistent references for caching and downstream integrations. They must
remain stable when a function moves within a file.

## Caller workflows

### Build a snapshot

```python
codebase = Codebase.analyze(files=files, frontends=[VbaFrontend()])
for diagnostic in codebase.diagnostics:
    report(diagnostic)
```

The analysis is deterministic. Files are routed to exactly one frontend, functions are
collected before calls are resolved, and reliable partial results remain available when
other files have diagnostics.

### Locate code

Use `source_files` and `find_source_files()` to enumerate or filter files. Use
`source_file()` and `functions_in_file()` for exact file access. Use `function()`,
`find_functions()`, and `functions_at()` for function lookup.

These APIs deliberately return domain objects rather than parser-specific structures, so
callers can use the same integration code across supported languages.

### Extract dependencies

Use `calls_from()` for call-site evidence, `calls_to()` for incoming call facts, and
`callees()`/`callers()` for graph traversal. Direct edges only contain reliable targets;
unresolved calls remain visible as facts and diagnostics.

### Build bounded context

Confirm entry points explicitly, then call `context_for()` with optional
`ContextLimits`. The context is suitable for documentation, indexing, review, or RAG
pipelines. CodeGraph does not decide tokenization or downstream formatting.

## Extension boundary

A language frontend owns language syntax, name resolution, case sensitivity, overloads,
packages, entry hints, and language diagnostics. The core owns routing, aggregation,
validation, indexing, traversal, and entry management.

Filesystem/project discovery, change tracking, CLI behavior, serialization, reports, and
downstream AI services should be implemented as separate adapters.

## Reliability principles

1. Do not infer a dependency from a name when the target is ambiguous.
2. Preserve unresolved facts and diagnostics instead of silently dropping them.
3. Keep IDs, ordering, ranges, and diagnostic codes deterministic.
4. Expose partial results explicitly so callers control blocking policy.
5. Keep language-specific behavior inside the corresponding frontend.
