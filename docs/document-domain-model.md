# Entry-scoped update model

## Problem

Code analysis determines which entry points are affected by a change. LLM analysis stays
entry-scoped, while publication groups independently generated entry results by their
canonical source file. This keeps prompts small without allowing one entry update to
overwrite another entry stored in the same Markdown document.

Smaller LLMs also produce more reliable results when each request contains facts for one
entry only. `document_updater` therefore converts each affected entry into one
independent, deterministic update plan and one reference-data payload.

## Public primitives

| Name | Meaning | Identity | Invariant |
| --- | --- | --- | --- |
| `Entry` | A CodeGraph behavior that can be affected by a change. | Stable `entry_id`. | Read-only code-analysis fact with no document state. |
| `EntryImpactContext` | One affected entry's change evidence plus baseline and working dependency contexts. | Its `entry_id`. | Both contexts remain traceable to the corresponding complete code snapshot. |
| `EntryUpdatePlan` | A single entry's deterministic update work item. | Its `entry_id`. | Contains exactly one `EntryImpactContext`, related function/call-edge changes, diagnostics, and action. |
| `PromptReference` | Action-specific data projection supplied to an LLM. | Its contained `entry_id`. | Contains one entry only and no document location or coverage metadata. |
| `EntryDocumentResult` | LLM-produced Markdown body for one plan. | Stable `entry_id`. | Cannot contain reserved document-management markers. |
| `SourceDocumentTarget` | Deterministic source-to-document mapping. | Canonical `source_id`. | Maps below `docs/` and cannot escape the documentation root. |
| `DocumentSyncPlan` | Fully validated publication work. | One analysis run. | Contains at most one final mutation per source document plus non-published review results. |

## Entry planning flow

```text
complete source snapshots
  -> CodeGraph extracts entries
  -> ChangeAnalyzer compares baseline and working graphs
  -> EntryImpact for every affected entry
  -> create_entry_plans()
  -> one EntryUpdatePlan per entry_id
  -> one LLM result per automatable entry
  -> group results by source_id
  -> one final write/delete mutation per source document
```

`create_entry_plans(report)` sorts affected entry IDs and creates exactly one plan for
each. Function changes, call-edge changes, and diagnostics are scoped to that entry's
baseline and working dependency contexts and remain deterministically ordered.

The action is derived only from that entry's presence in the two code graphs:

| Action | Meaning |
| --- | --- |
| `create` | The entry is new in the working graph. |
| `update` | The entry exists in both snapshots and has related change evidence. |
| `archive` | The entry only exists in the baseline graph. |
| `review` | Its contexts contain an error diagnostic, so it should not be automatically published. |

## Source-scoped document workflow

`resolve_document_targets()` derives the target from the entry function's canonical
`source_id`. A source such as `forms/frmOrder.frm` maps to
`docs/forms/frmOrder.md`. Mapping collisions and unsafe paths are errors.

Each entry is stored in a managed Markdown region:

```markdown
<!-- codegraph:source source_id="forms/frmOrder.frm" -->
# frmOrder.frm

<!-- codegraph:entry:start entry_id="vba:frmOrder:bSave_Click" -->
## bSave_Click

Saves the current order.
<!-- codegraph:entry:end entry_id="vba:frmOrder:bSave_Click" -->
```

The LLM supplies only the entry body. The synchronization layer owns source headers,
entry headings, and markers. It strictly rejects malformed, nested, duplicated, or
injected markers instead of falling back to a whole-file rewrite.

For LLM use, callers may pass current content as `old_document` and compose a Markdown
template themselves. When a physical document contains multiple entries, extract only
the current entry:

```python
old_entry_document = extract_entry_document(
    current_document,
    source_id=target.source_id,
    entry_id=plan.entry_id,
)
context = build_llm_context(plan, old_document=old_entry_document or "")
reference_data = context.to_reference_data_xml()
prompt = template.replace("{{ reference_data }}", reference_data)
```

The XML includes the action, one `entry_id`, available code contexts, impact paths, and
relevant changes. It deliberately excludes document paths, catalog data, and any document
publication state.

After all LLM calls complete, `build_document_sync_plan()` validates results, groups
plans by target source document, and calculates mutations entirely in memory:

| Action | Result requirement | Publication behavior |
| --- | --- | --- |
| `create` | Required | Add a new entry region; fail if it already exists. |
| `update` | Required | Replace only the matching region; fail if it is absent. |
| `archive` | Not accepted | Remove the matching region; delete the file if no managed or human-owned content remains. |
| `review` | Optional | Produce a pending review and never mutate the published document. |

Unmanaged preamble and trailing content are preserved. Non-whitespace content between
managed entry regions is rejected because its ownership would be ambiguous. Managed
sections are rendered in stable `entry_id` order.

## Boundary

`document_updater` owns entry-impact projection, reference-data serialization, pure
source-document composition, and the mutation model. `code_graph` does not understand
documents and `file_tracker` does not understand call graphs. Physical publication is
still adapter-driven: `LocalDocumentStore` is a caller-side filesystem adapter, and
other callers may apply the same `DocumentSyncPlan` to a repository API or database.

All document mutations must succeed before advancing the FileTracker baseline. Filesystem
writes are atomic per file, but publication across multiple files is not globally atomic.
