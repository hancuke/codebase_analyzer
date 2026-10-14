# Document run and recovery model

The recovery unit is one complete result identified by document_key. The caller
reanalyzes sources on every run and uses `Workbench.contains(key)` to skip costly
generation for committed results. `save()` persists complete bytes before
committing success. One JSON stores only the success index; result files are
separate. `results()` returns all committed results, not a completeness judgment.

There are no saved inputs, task plans, Batch, metadata, executor, phase, or status
objects. The caller guarantees unchanged inputs within a batch and resets the
workbench when inputs change. The package has no dependency on source-analysis
or document-update packages. Explicit `publish(document_root)` owns copying
results and cleaning up after successful publication. It does not advance a
FileTracker baseline; that is a separate caller step.

See [document recovery](document_recovery.md) for the API and interaction flow.
