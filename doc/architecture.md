# CodeGraph Architecture

## Architectural goal

CodeGraph is a small static-analysis core with a stable public boundary:

```text
caller-provided source snapshot
        |
        v
frontend routing and analysis
        |
        v
validated functions, calls, diagnostics
        |
        v
file/function indexes and dependency graph
        |
        v
composable caller queries
```

The core does not access the filesystem, execute code, expose parser ASTs, or link
relationships across languages.

## Responsibilities

| Layer | Responsibility |
| --- | --- |
| Caller adapters | Discover files, read projects, serialize results, render reports. |
| Public facade | Accept snapshots and frontends; expose domain queries and results. |
| Core domain | Validate facts, index files/functions/calls, traverse dependencies, and manage entries. |
| Language frontend | Parse one language, resolve reliable same-language calls, emit entry hints and diagnostics. |

Language frontends are the only language-specific extension point. A frontend may analyze
a batch of files from its language to support forward and cross-file references.

## Analysis flow

```text
SourceFile[]
   -> validate unique paths
   -> route each file to exactly one frontend
   -> analyze frontend batches
   -> aggregate functions, calls, entry hints, diagnostics
   -> validate call sources and targets
   -> build file/function and forward/reverse indexes
   -> expose Codebase
```

All functions are collected before calls are resolved. Confirmed calls become graph edges;
unresolved calls remain available through call facts and diagnostics.

## Public model rules

- `SourceFile.path` is the snapshot identity.
- `Function.id` is stable and unique within the codebase.
- `SourceRange` is one-based and inclusive.
- `Call.target_id` is present only for reliable resolution.
- Diagnostics are structured data, not logs.
- Results are deterministic and use immutable value objects.

## Error strategy

Expected input and semantic problems become diagnostics. Unknown function and source-file
lookups use explicit domain exceptions. Unexpected implementation failures must propagate
with context rather than being hidden behind empty results.

## External adapters

Project discovery, directory walking, Git integration, file watching, JSON schemas, CLI
commands, reports, and AI/RAG integrations should be separate packages or applications.
They may compose the core API but must not force their concerns into the static-analysis
domain model.
