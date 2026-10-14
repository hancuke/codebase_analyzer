# Document analysis lifecycle

See [document recovery](document_recovery.md) for the workbench design and the
document-unit checkpoint flow. The caller owns source scanning, analysis,
generation, and publication; `document_workbench` persists the fixed run inputs
and completed document-unit outputs so the caller can resume after interruption.
