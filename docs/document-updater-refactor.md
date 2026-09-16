# Document updater refactor

## Goal

Keep document updating composable by separating three concerns:

```text
change_analyzer: EntryChange facts
caller workflow: content generation, document ownership, and I/O
document_updater: managed Markdown fragment mutation
```

No package owns an implicit “Entry documentation synchronization” policy.

## Minimal public API

`document_updater` has exactly one domain concept: a stable fragment's desired state.

```python
class ResultKind(Enum):
    UPSERT = "upsert"
    DELETE = "delete"

@dataclass(frozen=True)
class DocumentResult:
    fragment_id: str
    kind: ResultKind
    markdown: str | None = None

def get_fragment_markdown(markdown: str, fragment_id: str) -> str | None: ...
def apply_result(markdown: str, result: DocumentResult) -> str: ...
```

`UPSERT` contains the entire fragment body; `DELETE` has no body. `apply_result()` is pure
and deterministic: it parses canonical markers, inserts/replaces/removes one fragment, and
returns the resulting Markdown. Callers compare input and output when they need to know whether
it changed. It preserves caller-owned Markdown outside the managed fragment region and rejects
malformed or ambiguous marker input.

This package does **not** include Entry plans, source paths, document keys, stores, prompt
blocks, prompt renderers, LLM clients, authors, perspectives, mergers, review queues, or
batch publication types. Those terms describe caller-specific policies, not fragment mutation.

## Caller composition

The caller owns a mapping from document key to `DocumentResult` values. A key with an empty
result collection is a participating document with no mutation. Before publishing, it verifies
that every fragment ID occurs at most once per document, folds `apply_result()` in its chosen
stable order, then persists the Markdown. It may remove an empty physical document and advances
the FileTracker baseline only after all publishing succeeds.

Content producers are independent. For example, an Entry producer can use `EntryChange` and an
LLM while a UI-control producer derives a table from another source. They only need to agree on
the caller-owned document key and use distinct stable fragment ID namespaces.

For LLM generation, the caller selects 1..N ordered `(system_prompt, user_prompt)` pairs for an
Entry, calls its provider, combines the completions, and creates one `UPSERT DocumentResult`.
Prompts must have bounded outputs and non-overlapping responsibilities. More elaborate
synthesis remains a caller workflow concern.

When an Entry fragment is absent, the caller prompts from the current bounded `new_context`
only. A diff, old code, and old fragment duplicate or contradict the source of truth. When the
fragment exists, the caller may add the existing body, scoped change summary, and deletion facts
that current code cannot express. A deleted Entry produces `DELETE` directly without any model
call.

## Dependency direction

```text
file_tracker       code_graph
      \             /
       change_analyzer
              |
       caller workflow
              |
       document_updater
```

`document_updater` never depends on the analysis packages, physical paths, file I/O, or a model
provider. This keeps its desired-state and Markdown invariants independently testable.
