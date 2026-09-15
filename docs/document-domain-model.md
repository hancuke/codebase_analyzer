# 文档更新领域模型

文档更新由三个独立领域组合，而不是由一个 Entry 同步服务隐式完成。

| 领域 | 所有者 | 输入 | 输出 |
| --- | --- | --- | --- |
| 代码变更影响 | `change_analyzer` | ChangeSet、完整工作源码、analyzers | `EntryChange[]` |
| 文档内容生成与 fragment 编辑 | `document_updater` | `AuthorRequest` 或一份 Markdown与 `DocumentResult` | `DocumentResult` 或 `AppliedDocument` |

## EntryChange

`entry_changes(report)` 从 `ImpactReport` 投影出一个 Entry 一个事实对象。它在 baseline 和
working 图中同时反向遍历，因而保留删除调用、删除函数和新增依赖的影响证据。对象包含 old/new
Entry 和 dependency context、函数与调用边变化、path evidence 及 diagnostics。

它不包含文档动作、LLM、路径或发布规则。

## AuthorRequest 和 DocumentResult

应用层将一个或多个 `EntryChange` 转换为：

```python
AuthorRequest(
    fragment_id="entry:vba:frmOrder:bSave_Click",
    instructions="...",
    blocks=(PromptBlock("code_context", "..."),),
)
```

`DocumentAuthor` 只渲染 ordered blocks 并调用 LLM，返回 `DocumentResult`。它与
`apply_result()` 共同位于 `document_updater` package。`UPSERT` 表示该
fragment 的完整 Markdown 期望状态；`DELETE` 表示该 fragment 不应存在，无需 LLM。

多个 Entry 是否合成一次请求、哪些 diagnostics 转为审核，都是应用层策略。

## Fragment 编辑

`apply_result(existing_markdown, result)` 是纯单文档操作。它通过 canonical
`codegraph:fragment` markers 识别受管内容：UPSERT 插入或替换，DELETE 幂等删除。Markdown
前后的人工内容保持不变；fragment 之间的无归属内容、损坏或注入的 marker 会被拒绝。

`AppliedDocument.fragment_ids` 只描述剩余受管 fragments。物理文件是否应删除、路径映射、加载和
写入完全由调用方决定。
