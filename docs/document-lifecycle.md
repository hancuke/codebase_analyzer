# Document analysis lifecycle

See [document recovery](document_recovery.md) for the workbench design and the
document-unit checkpoint flow. The caller owns source scanning, analysis,
generation, and optional baseline advancement. `Workbench.open(root)` creates or
restores the successful-result index. Every run scans and analyzes again; the
caller skips expensive generation when `contains(document_key)` is true and
submits complete bytes with `save()`. Success is committed only after the result
has been saved. `results()` returns checked saved values, not internal paths.
The caller owns completeness and must finish generation before calling
`publish(document_root)`.

Explicit publish copies results with per-file atomic replacement, then closes
and cleans up the workbench. Publication failures preserve the results for retry.
Normal context-manager exit only releases the lock, without publishing.
FileTracker commit is a separate caller step; publication does not scan or
advance the baseline. There is no transaction between publishing and baseline
advancement. Cleanup failures are explicit and may need manual completion.
Input changes require a reset.
