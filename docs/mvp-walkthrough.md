# Entry 文档更新 MVP

`change_analyzer` 比较 baseline 与 working 的完整源码图，并通过两张图中的反向可达关系找出受影响
Entry。`entry_changes(report)` 是把这项复杂图分析交给文档工作流的唯一入口。

随后应用层将每个 `EntryChange` 或选定的 Entry 集合转换为自身的 prompt 和
`DocumentResult`；最后按应用层的文档分组策略，逐个调用
`document_updater.apply_result()`。每层只表达自己的事实和规则。

Access/VBA 示例的 `affected_entries_by_form()` 按“单个 Form + 共享 Module”分析，假定不同
Form 的 VBA 不互相调用，不把所有 Form 合并到同一名称解析范围。一次调用中，共享 Module
只读取一次，通过 `VbaAnalyzer(cache_size=4096)` 复用相同源码的函数和未解析调用（每个缓存最多
4096 项，超出后按 LRU 淘汰）；旧/新快照及各 Form 的名称解析、diagnostics 和影响路径仍独立
计算。示例每次调用创建新 analyzer，不跨 scan 保留缓存或缓存已解析图。
