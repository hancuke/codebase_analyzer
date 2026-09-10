# Entry-oriented documentation update MVP

This workspace demonstrates the proposed incremental documentation flow:

```text
FileTracker ChangeSet
    -> baseline and working SourceFile snapshots
    -> old and new CodeGraph
    -> function and call-edge changes
    -> affected entry points with old/new path evidence
    -> connected impact batches for independent change groups
    -> one documentation plan per affected document
    -> LLM-ready context
```

## Packages

- `code_graph/`: extracts functions, calls, entry points, and dependency contexts.
- `file_tracker/`: captures immutable file changes and baseline revisions.
- `change_analyzer/`: reconstructs two complete source snapshots and maps graph changes
  to affected entries.
- `document_updater/`: converts entry impacts into document actions and prompt context.

The two new packages are orchestration layers. Neither change tracking nor documentation
behavior is added to the CodeGraph core.

## Run the example

```bash
uv sync --all-packages
uv run python examples/entry_document_update.py
```

The example creates a temporary VBA project with two button-entry procedures sharing
`CheckPermission`. It commits an initial FileTracker baseline and then:

1. modifies the shared `CheckPermission`;
2. modifies `SaveOrder`;
3. adds `AuditOrder`;
4. scans the physical file changes;
5. builds old and new CodeGraph snapshots;
6. shows that the shared change affects both entry documents;
7. shows that the order-only changes affect only the save-button document;
8. separates an unrelated report refresh change into a second impact batch;
9. renders one LLM-ready update context.

## Important MVP behavior

`analyze_changes()` requires the complete current source snapshot, not only changed files:

```python
report = analyze_changes(
    change_set=file_tracker.scan(),
    working_sources=all_current_source_files,
    analyzers=[VbaAnalyzer()],
)
```

It overlays the immutable `FileChange` content onto that complete snapshot to reconstruct
the baseline. This allows an unchanged form entry to be discovered as a caller of a
changed module function.

For each changed function, affected entries are the union of:

```text
entries that reached the function in the baseline graph
UNION
entries that reach the function in the working graph
```

Using both graphs preserves the impact of deleted functions and removed call edges while
also finding newly introduced dependencies.

`create_document_plans()` then resolves each affected entry through the versioned
`DocumentCatalog` and produces one plan per affected document. Multiple entries of the
same Access Form can therefore share one plan and one document; each entry still retains
its own old/new path evidence and dependency context. A shared function across Forms
intentionally appears in each affected Form document plan. See
[`document-domain-model.md`](document-domain-model.md) for the minimal `Entry`,
`Document`, `Coverage`, and `DocumentCatalog` model.

## Current boundaries

This MVP compares function source and resolved call edges. It preserves functions with no
reachable entry in `ImpactReport.unassigned_changes`. It does not yet classify non-function
Access form properties, infer renames, resolve dynamic calls, publish generated documents,
or advance the FileTracker baseline automatically.
