# CodeGraph 设计规格

## 它是什么

`codegraph` 用来回答一个简单的问题：

> 当某个函数执行时，它会调用哪些其他函数？

它读取一组源代码文件，找出函数之间的调用关系。之后可以：

* 从一个入口函数收集它需要的全部代码；
* 找到某个函数被谁调用；
* 文件修改后，找出哪些入口需要重新分析；
* 将结果交给文档、LLM、RAG、CI 或其他上层程序。

这里的“函数”泛指可以执行的代码单元，例如 VBA 的 `Sub`、Java 的方法、PL/SQL 的 Procedure，或 Web Controller 的处理函数。

## 最简单的使用方式

调用者提供两类东西：

1. **代码文件**；
2. **语言前端**，即知道如何读取某种语言的插件。

```python
from codegraph import Codebase, SourceFile, VbaFrontend

codebase = Codebase.analyze(
    files=[
        SourceFile("frmOrder.bas", form_source),
        SourceFile("modOrder.bas", module_source),
    ],
    frontends=[VbaFrontend()],
)
```

`Codebase` 会完成其余工作：把文件交给合适的前端、收集函数、建立调用关系，并保存可查询结果。

调用者不需要切分函数，不需要手动构建图，也不需要理解编译器、AST 或 LSP。

## 核心概念

只需要认识四个概念。

| 名称 | 可以把它理解为 | 例子 |
| --- | --- | --- |
| `SourceFile` | 一份完整的代码文件 | `modOrder.bas` |
| `Function` | 文件中一段可调用的代码 | `SubmitOrder()` |
| `Call` | “这个函数调用了另一个函数” | `SaveOrder -> SubmitOrder` |
| `Codebase` | 已分析完成、可以查询的代码库 | 当前项目的所有文件 |

### SourceFile

```python
SourceFile(
    path="modOrder.bas",
    content=source_text,
    language="vba",  # 可选
)
```

`path` 是文件的唯一名称，用于刷新时替换或删除文件。`content` 必须是完整文件内容。

### Function

前端从文件中找到函数，并保存：

* 稳定的函数 ID；
* 函数名称；
* 所在文件和模块；
* 这段函数的原始代码；
* 函数在文件中的行号范围。

函数 ID 在同一个代码库中必须唯一，并且函数只是移动到别的行时不能改变。例如：

```text
vba:frmOrder:bSave_Click
vba:modOrder:SubmitOrder
java:OrderController#create(OrderRequest)
```

### Call

`Call` 表示在一个函数中发现了一次调用：

```text
bSave_Click -> SubmitOrder
```

前端能确定目标时，`Call` 会包含目标函数 ID，`Codebase` 因此建立可靠的调用关系。

前端无法确定目标时，仍应保留调用名称和位置，并输出诊断信息；不应猜测目标或建立可能错误的关系。

### Codebase

`Codebase` 是唯一的核心对象。它保存函数和调用关系，并提供查询、入口管理和刷新。

```python
save = "vba:frmOrder:bSave_Click"
submit = "vba:modOrder:SubmitOrder"

codebase.function(save)
codebase.callees(save)
codebase.callers(submit)
```

## 语言前端

不同语言的语法不同，所以每种语言由自己的 Frontend 负责理解。

```python
class LanguageFrontend(Protocol):
    def supports(self, file: SourceFile) -> bool:
        """这个前端是否能处理该文件？"""

    def analyze(self, files: Sequence[SourceFile]) -> FileAnalysis:
        """从完整文件中找出函数、调用和诊断。"""
```

前端可以是简单的文本解析器，也可以在内部使用成熟的 LSP、编译器或静态分析工具。调用者不需要知道它采用哪一种方式。

每个文件必须由**恰好一个**前端处理：

* 没有前端支持该文件：产生错误诊断；
* 两个前端都支持该文件：产生错误诊断；
* 前端处理多个同语言文件时：可以一次分析整批文件，因此可以识别跨文件调用。

### 前端要做什么

一个 Frontend 的输出是 `FileAnalysis`：

```python
@dataclass(frozen=True)
class FileAnalysis:
    functions: tuple[Function, ...]
    calls: tuple[Call, ...]
    entry_candidates: tuple[EntryCandidate, ...]
    diagnostics: tuple[Diagnostic, ...]
```

前端负责：

1. 从完整文件中找出函数的开始和结束；
2. 为函数生成稳定 ID；
3. 保存函数源码和行号范围；
4. 找出函数中的调用；
5. 在能够可靠判断时，将调用连接到目标函数；
6. 找出可能的入口，例如表单按钮事件或 HTTP 路由；
7. 报告无法正确理解的语法或代码。

核心不负责理解语言。例如，VBA 不区分大小写、Java 有方法重载、PL/SQL 有 Package；这些规则都应在相应 Frontend 中实现。

## 建立调用关系

`Codebase.analyze()` 按以下顺序工作：

```text
完整文件
  -> Frontend 找出函数和调用
  -> Codebase 收集所有函数
  -> Codebase 保存已确定的调用
  -> 可以查询代码库
```

所有函数先被收集，再保存调用关系。因此，一个文件可以调用另一个文件中的函数，也可以调用稍后声明的函数。

CodeGraph 只保存**直接调用**：

```text
SaveOrder -> ValidateOrder
ValidateOrder -> LoadCustomer
```

它不会额外保存 `SaveOrder -> LoadCustomer`。当需要全部依赖时，查询会沿着直接调用关系继续查找。

这让数据更少，也避免保存互相矛盾的重复关系。

## 查询代码

### 查询调用关系

```python
codebase.callees(function_id)
codebase.callers(function_id)
```

默认只返回直接关系。传入 `transitive=True` 时，返回所有间接关系：

```python
codebase.callees("vba:frmOrder:bSave_Click", transitive=True)
```

即使代码中有循环调用，查询也必须安全结束，并且函数不会被算作自己的调用者或被调用者。

### 从入口收集上下文

入口是业务开始执行的位置，例如：

* 表单按钮事件；
* HTTP 请求处理函数；
* 定时任务；
* 消息消费者；
* 调用者手工指定的过程。

前端只能提供“候选入口”，最终是否作为入口由调用者确认：

```python
# 接受前端发现的候选，例如 VBA 的按钮事件。
codebase.accept_entry_candidates()

# 按业务规则增加入口。
codebase.mark_entries(
    lambda function: function.attributes.get("is_controller", False),
    kind="controller",
)

# 完全替换当前入口。
codebase.set_entries(
    ["vba:frmOrder:bSave_Click"],
    kind="manual",
)
```

从入口获取上下文：

```python
context = codebase.context_for("vba:frmOrder:bSave_Click")
```

上下文包含：

| 内容 | 用途 |
| --- | --- |
| 入口函数 | 本次分析从哪里开始 |
| 所有可达函数 | 入口执行所需的代码及其源码 |
| 内部调用 | 这些函数之间的已确定关系 |
| 调用路径 | 从入口到最深依赖的路径 |
| 诊断信息 | 未识别调用或解析问题 |

CodeGraph 不调用 LLM，也不生成 prompt。上层程序可以按自己的 token 预算和提示词规则使用 `context` 中的函数源码。

## 文件刷新和影响分析

文件变更后，用新内容刷新：

```python
result = codebase.refresh([
    SourceFile("modOrder.bas", changed_source),
])
```

删除文件时：

```python
result = codebase.refresh(
    changed_files=[],
    removed_paths=["legacy/modOldOrder.bas"],
)
```

结果会包含：

* 修改、新增或删除的函数；
* 调用关系发生变化的函数；
* 受影响的已确认入口；
* 刷新后发现的诊断。

第一版每次刷新重新分析所有当前文件。这样虽然不是最快，但最容易保证正确：跨文件调用、类型信息或 LSP 结果不会因为局部更新而过期。

影响分析同时查看修改前和修改后的调用关系。例如删除 `ValidateOrder` 后，新代码已经没有对它的调用；但仍需要知道原来调用它的 `SaveOrder` 入口应重新分析。

## 诊断

诊断是分析结果的一部分，不是仅供开发者查看的日志。

常见诊断包括：

* 文件没有合适的 Frontend；
* 文件被多个 Frontend 同时处理；
* 两个函数使用同一个 ID；
* 调用来自不存在的函数；
* 调用目标不在当前代码库中；
* Frontend 无法理解部分源码；
* 调用名称存在，但无法可靠确定目标。

遇到不能确定的调用时，CodeGraph 选择“没有关系，并说明原因”，而不是“猜一个可能的关系”。这是为了避免错误影响分析和错误上下文。

## 维护边界

维护时只需遵守以下分工：

| 组件 | 只负责什么 |
| --- | --- |
| Frontend | 读懂特定语言，输出函数和调用 |
| Codebase | 保存函数与调用，提供查询、入口和刷新 |
| 上层应用 | 展示结果、调用 LLM、生成文档、执行 CI |

`Codebase` 内部可以用字典保存函数，并用正向、反向索引保存调用关系。它们是实现细节，不需要成为调用者或插件作者要操作的对象。

如果未来需要更多语言，应新增 Frontend；如果未来需要更快的刷新，应优化 `Codebase` 的内部重建策略。两者都不应增加调用者需要理解的概念。

## 验收标准

实现满足以下条件才符合本规格：

1. 调用者只需提供完整文件和语言 Frontend；
2. Frontend 能找出函数、源码范围、调用和诊断；
3. 每个文件只由一个 Frontend 分析；
4. 函数 ID 稳定且唯一；
5. 跨文件和前向调用不依赖文件顺序；
6. 只有已确定的内部调用进入调用关系；
7. 查询能处理循环调用；
8. 调用者能管理入口并获得入口上下文；
9. 刷新后不会保留已删除函数或过期调用；
10. 删除调用或函数仍能找到此前受影响的入口。
