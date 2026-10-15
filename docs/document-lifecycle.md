# Document analysis lifecycle

See [document recovery](document_recovery.md) for the workbench design and the
document-unit checkpoint flow. The caller owns source scanning, analysis,
generation, and optional baseline advancement. `Workbench.open(root)` creates or
restores the successful-result index. Every run scans and analyzes again; the
caller skips expensive generation when `contains(document_key)` is true and
submits complete UTF-8 text with `save()`. Success is committed only after the result
has been saved. `results()` returns checked saved values, not internal paths.
The caller owns completeness and must finish generation before calling
`publish(document_root)`.

Explicit publish copies results with per-file atomic replacement, then closes
and cleans up the workbench. Publication failures preserve the results for retry.
No lock or directory-occupancy detection is used. The caller guarantees one
instance per document root, with independent, non-overlapping roots for different
instances. The example derives its store as `document_root / ".workbench"` and
document discovery must exclude that directory.
`prepare_batch()` returns a Workbench that can cross function boundaries before
explicit publication; `run_batch()` prepares and immediately publishes.
No context manager or resource-release step is required. `close()` only
invalidates the object, without publishing or deleting results. The optional
context-manager interface remains compatible and closes the object on exit.
FileTracker commit is a separate caller step; publication does not scan or
advance the baseline. There is no transaction between publishing and baseline
advancement. Cleanup failures are explicit and may need manual completion.
Input changes require a reset.

For multi-phase A/B generation, Workbench persists each finished task's ordered
`tuple[DocumentResult, ...]` under a document/task pair. Reopening skips committed pairs, including when
a phase was interrupted. The caller confirms all phases complete and invokes
`assemble(document_root)` to read originals, apply restored task results in
task-ID order, and save complete text without changing originals.
Missing originals are empty; read and mutation errors propagate. The first full-document save
automatically freezes task collection before output writing; further task writes
are rejected after recovery as well. Assembly retries skip saved documents and
publication retries reuse them. Only `.workbench` is needed; task results live in
its state JSON and are never published. The workbench's frozen `TaskResult` owns
checkpoint serialization and validation without changing `DocumentResult`;
Workbench delegates document mutation to `document_updater.apply_results`.
`publish(document_root)` automatically assembles unsaved task documents before
writing any destinations. Callers can omit the explicit assembly call, or keep
it to inspect saved results before publishing. Assembly failure publishes nothing;
publication retries reuse saved results without reassembly.
