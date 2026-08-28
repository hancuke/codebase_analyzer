# CodeGraph MVP 使用指南

当前 MVP 提供完整文件分析、VBA `Sub`/`Function` 发现、可信的同语言调用图、入口上下文和全量刷新影响分析。它不调用 LLM，也不生成 prompt。

## 最小 VBA 示例

```python
from codegraph import Codebase, SourceFile, VbaFrontend

codebase = Codebase.analyze(
    files=[
        SourceFile(
            "frmOrder.frm",
            """
Private Sub bSave_Click()
    If ValidateOrder() Then
        Call SaveOrder
    End If
End Sub
""",
        ),
        SourceFile(
            "modOrder.bas",
            """
Public Function ValidateOrder() As Boolean
End Function

Public Sub SaveOrder()
End Sub
""",
        ),
    ],
    frontends=[VbaFrontend()],
)
```

`Codebase.analyze()` 返回可查询的快照。不能解析的文件或调用不会阻止其他可靠结果产生，但会出现在 `codebase.diagnostics`。

## 输入与诊断

`SourceFile.path` 是快照内的唯一身份；刷新时以它替换或删除文件。`content` 必须是完整源码，`language="vba"` 可在扩展名不明确时指定 VBA 前端。

```python
for diagnostic in codebase.diagnostics:
    print(diagnostic.severity, diagnostic.code, diagnostic.message)
```

MVP 会报告重复文件路径、无支持前端、多前端竞争、重复函数 ID、无效调用源/目标和无法解析的 VBA 调用。未解析调用保留在 `calls_from()` 的结果中，但不会成为依赖图边。

## 查询函数和调用

函数 ID 在同一代码库中唯一，并且不随其行号移动而改变：

```python
save = "vba:frmOrder:bSave_Click"

function = codebase.function(save)
direct_dependencies = codebase.callees(save)
all_dependencies = codebase.callees(save, transitive=True)
direct_callers = codebase.callers("vba:modOrder:ValidateOrder")
calls_in_source_order = codebase.calls_from(save)
```

`callees()` 和 `callers()` 返回 `Function`；默认只返回直接关系。`calls_from()` 返回包含源码位置、原始调用名称、已解析目标（如有）和证据的 `Call`。循环调用的传递查询会安全结束，且不会把查询起点作为自己的结果。

未知函数 ID 会抛出 `FunctionNotFoundError`。如只需探测是否存在，可使用 `get_function()`，它会返回 `None`。

## 管理入口和获取上下文

VBA 名称以 `_Click` 结尾的过程会作为候选入口，但只有调用者确认后才成为入口：

```python
codebase.accept_entry_candidates()

# 或由业务规则追加入口。
codebase.mark_entries(
    lambda function: function.attributes.get("visibility") == "public",
    kind="public_procedure",
)

# 替换为手工确认的入口。
codebase.set_entries(["vba:frmOrder:bSave_Click"], kind="form_event")

context = codebase.context_for("vba:frmOrder:bSave_Click")
for function in context.functions:
    print(function.qualified_name, function.source)
```

`AnalysisContext` 包含入口、可达函数、这些函数之间的已确定调用、每个函数的一条入口路径以及相关诊断。尚未实现来源大小、函数数量或深度预算；上层可先使用 `context.functions` 自行裁剪。

## 刷新和影响入口

刷新始终重新分析当前全部文件，以保证跨文件调用不会过期：

```python
result = codebase.refresh(
    changed_files=[
        SourceFile("modOrder.bas", changed_module_source),
    ],
    removed_paths=["legacy/modOldOrder.bas"],
)

for function_id in result.changed_function_ids:
    print("changed:", function_id)
for entry in result.affected_entry_points:
    print("reanalyze:", entry.function_id)
```

`RefreshResult` 列出函数变化、出边变化的源函数、刷新诊断和受影响的**刷新前已确认**入口。影响计算同时检查旧图和新图，因此删除函数或删除调用不会漏掉先前依赖它的入口。

## 扩展状态

[`LanguageFrontend`](frontend.md) 是公开协议：它需要实现 `supports(SourceFile)` 和批量 `analyze(Sequence[SourceFile]) -> FileAnalysis`。核心会保证支持的每份文件只交给一个前端。

MVP 自带 `VbaFrontend`，支持 `.bas`、`.cls`、`.frm` 文件中的 `Sub` 和 `Function`。它使用 Pygments 进行 token 化，忽略注释和字符串后识别常见 VBA 调用形式，再按 VBA 大小写不敏感的简单名称解析跨文件和前向调用；重名或未知目标会产生 `unresolved_call`，不会猜测建立边。

跨语言链接器、HTTP/RPC/IDL 证据链接、复杂 VBA 语法、方法重载和上下文预算属于后续扩展，详见 [`design.md`](design.md) 与 [`architecture.md`](architecture.md)。
