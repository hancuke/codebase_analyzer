# Document run and recovery model

The recovery unit is one complete target-document update. The caller groups
language-specific analysis results by target document; `document_workbench`
stores the fixed inputs, unit plan, completed outputs, and integrity metadata.
It does not depend on source-analysis or document-update packages.

See [document recovery](document_recovery.md) for the API and interaction flow.
