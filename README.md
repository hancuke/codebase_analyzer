# Code documentation workspace

此 uv workspace 将源代码变化转换为可组合的文档 fragment 更新。它不规定一个 Entry 必须生成一份
文件，也不将 LLM、Markdown 编辑和文件发布耦合在同一个包中。

```text
FileTracker ChangeSet + complete source snapshot
  -> change_analyzer.analyze_changes()
  -> change_analyzer.entry_changes()
  -> caller-defined AuthorRequest
  -> document_updater.DocumentAuthor
  -> DocumentResult
  -> caller groups results by document
  -> document_updater.apply_result()
  -> caller-owned storage and baseline commit
```

## Workspace packages

| Package | Responsibility |
| --- | --- |
| `code_graph/` | 从完整源码快照提取函数、调用、入口、diagnostics，并提供确定性图查询。 |
| `file_tracker/` | 跟踪物理文件变化并维护 immutable baseline transaction。 |
| `change_analyzer/` | 比较旧/新 CodeGraph，并将函数变更投影为具有双图路径证据的 `EntryChange`。 |
| `document_updater/` | 将 prompt blocks 渲染并通过 LLM 生成 fragment result，同时将 desired state 纯函数式地应用到 Markdown。 |

依赖保持单向：

```text
file_tracker       code_graph
      \             /
       change_analyzer
              |
      caller workflow
              |
      document_updater
```

`document_updater` 不依赖代码分析、FileTracker、文件路径或文件系统。

## Minimal APIs

### Code change to affected Entries

```python
from change_analyzer import analyze_changes, entry_changes

report = analyze_changes(change_set, working_sources, analyzers)
changes = entry_changes(report)
```

每个 `EntryChange` 包含 old/new Entry 和 dependency context、可达函数/调用边变化、diagnostics 与
old/new path evidence。Entry 查找使用 baseline 和 working 两张图的反向可达并集，因此删除的依赖和
新增的依赖都会影响正确的 Entry。

### Context to one document result

```python
from document_updater import AuthorRequest, LlmDocumentAuthor, PromptBlock

request = AuthorRequest(
    fragment_id="entry:vba:frmOrder:bSave_Click",
    instructions="Write concise technical documentation.",
    blocks=(PromptBlock("code_context", rendered_context),),
)
result = author.author(request)
```

`DocumentAuthor` 返回 `DocumentResult(kind=ResultKind.UPSERT, ...)`。删除的内容由调用方直接构造
`DocumentResult(fragment_id, ResultKind.DELETE)`，无需调用 LLM。

### One result to one Markdown document

```python
from document_updater import apply_result

applied = apply_result(existing_markdown, result)
```

受管内容采用 `codegraph:fragment` markers。UPSERT 在缺失时插入、存在时替换；DELETE 幂等删除。
`AppliedDocument.fragment_ids` 仅报告剩余受管 fragment，物理文件是否应删除由调用方决定。

调用方拥有 fragment 的来源、`fragment_id -> document key` 映射、分组、Markdown 加载/发布、审核队列
与 FileTracker baseline 推进。支持一 Entry 一 fragment、多个 Entry 合成一个 fragment，或多个
fragment 合并到一个文档。

## Runnable lifecycle example

```bash
uv run python examples/documentation_lifecycle.py
```

该示例在临时目录中依次演示：

1. 建立 VBA 源码 baseline；
2. 修改一个被 Entry 调用的依赖，使用 `entry_changes()` 找到受影响 Entry；
3. 将 Entry facts 转换为 `AuthorRequest`，并通过一个 deterministic mock LLM 生成 UPSERT result；
4. 由调用方将 fragment 映射到 `docs/frmOrder.md`、调用 `apply_result()` 并写入文件；
5. 删除 Entry，依据 old context 生成 DELETE result，并由调用方在文档为空时删除物理文件；
6. 每次所有文档成功发布后才推进 FileTracker baseline。

## Setup and validation

Python 3.10+ 与 uv 是必需条件。

```bash
uv sync --all-packages
uv run pytest code_graph/tests file_tracker/tests change_analyzer/tests document_updater/tests
uv build --all-packages
```

领域边界与完整生命周期见：

- [`docs/document-domain-model.md`](docs/document-domain-model.md)
- [`docs/document-lifecycle.md`](docs/document-lifecycle.md)
- [`docs/mvp-walkthrough.md`](docs/mvp-walkthrough.md)
