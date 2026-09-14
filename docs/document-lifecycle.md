# 文档生命周期

```text
analyze_changes(...)
  -> entry_changes(report)
  -> caller creates AuthorRequest or DELETE DocumentResult
  -> DocumentAuthor.author(request)
  -> caller groups results by its DocumentKey
  -> apply_result(markdown, result) for each result
  -> caller persists all documents
  -> caller advances FileTracker baseline
```

调用方对 `fragment_id -> DocumentKey` 负责。这允许一 Entry 一 fragment、多 Entry 合成一个
fragment，或多个 fragment 合并到一个文档，而不改变 `change_analyzer`、`document_author` 或
`document_updater`。

只有所有 authoring、fragment 应用和物理发布成功后，调用方才能用 `ImpactReport` 的 revisions
推进 FileTracker baseline。审核队列是调用方 workflow 状态，不是文档 mutation。

可运行的完整示例位于
[`examples/documentation_lifecycle.py`](../examples/documentation_lifecycle.py)：

```bash
uv run python examples/documentation_lifecycle.py
```

它使用 deterministic mock LLM，因此不需要网络凭据，并展示依赖修改的 UPSERT 和 Entry 删除的
DELETE 两条路径。
