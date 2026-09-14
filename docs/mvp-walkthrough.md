# Entry 文档更新 MVP

`change_analyzer` 比较 baseline 与 working 的完整源码图，并通过两张图中的反向可达关系找出受影响
Entry。`entry_changes(report)` 是把这项复杂图分析交给文档工作流的唯一入口。

随后应用层将每个 `EntryChange` 或选定的 Entry 集合转为 `AuthorRequest`；`document_author` 返回一
个 `DocumentResult`；最后按应用层的文档分组策略，逐个调用
`document_updater.apply_result()`。每层只表达自己的事实和规则。
