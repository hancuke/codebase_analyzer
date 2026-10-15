# Document run and recovery model

The recovery unit is one complete result identified by document_key. The caller
reanalyzes sources on every run and uses `Workbench.contains(key)` to skip costly
generation for committed results. `save()` persists complete UTF-8 text before
committing success. One JSON stores the document index, serialized fragment results and
the frozen-task flag; complete document files are
separate. `results()` returns all committed results, not a completeness judgment.

There are no saved inputs, task plans, Batch, metadata, executor, phase, or status
objects. The caller guarantees unchanged inputs within a batch and resets the
workbench when inputs change. The package has no dependency on source-analysis
packages. It depends on `document_updater.DocumentResult` as the fragment value type,
and delegates fragment mutation to `apply_results` during assembly.
Explicit `publish(document_root)` owns copying
results and cleaning up after successful publication. It does not advance a
FileTracker baseline; that is a separate caller step.

Workbench is a stateful object, not a locked resource. Callers can pass it between
functions and publish later; close only invalidates the object. Atomic state
replacement and integrity checks still protect serial recovery, not concurrent
access. Callers guarantee exclusive use of each store and publication directory;
concurrent access is unsupported and is not detected. In the example, independent,
non-overlapping document roots each own a `.workbench` child directory, excluded
from document discovery and publication keys.

See [document recovery](document_recovery.md) for the API and interaction flow.

Workbench also provides task-item checkpoints keyed by `(document_key, task_id)`:
each pair commits an ordered `tuple[DocumentResult, ...]`, including an empty tuple.
The frozen `TaskResult` checkpoint owns the document/task identity, result tuple,
and `to_dict()` / `from_dict()` serialization. `DocumentResult` stays unchanged.
Workbench owns atomic persistence.
Multiple phases may contribute to the same document. Completed task items do not
imply complete documents. The first full-document save persists a frozen task
set before writing the document. Later task additions are rejected, including
after recovery. The caller confirms all phases complete and invokes
`assemble(document_root)`, which reads unsaved originals (missing means empty),
merges task results in task-ID order, and saves full text without publishing.
Retries skip saved documents; read and mutation errors propagate.
`publish(document_root)` automatically assembles unsaved task documents before
publishing any full text. Assembly failures publish nothing; publication retries
reuse saved documents. Publication publishes no task payloads and cleans all
checkpoints together. Separate assembly remains available for inspecting results.
There is no separate session object or explicit sealing API.
