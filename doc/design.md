# CodeGraph 交互设计：调用者旅程与能力边界

> 本文是 API 设计讨论稿，不是实现承诺。代码片段用于说明交互，不要求当前项目已有对应类或方法。

## 想解决的事

调用者的问题不是“如何建一张图”，而是：

> 某个业务入口执行时，会经过哪些代码？代码变化后，哪些业务入口需要重新理解？

调用者应只需要交付完整源码文件、选择可处理这些文件的语言前端，然后围绕函数、调用、入口和上下文工作。解析器、AST、LSP、图索引、缓存和增量算法都不应成为普通调用者的概念。

VBA 是第一个完整场景：用户单击表单的保存按钮，依次执行校验和模块过程。设计不将这种场景写死；Java Controller、PL/SQL Procedure、定时任务和消息消费者都应能使用相同主流程。

## 一眼能看懂的主流程

```python
analysis = Codebase.analyze(
    files=[
        SourceFile("frmOrder.frm", form_source),
        SourceFile("modValidation.bas", validation_source),
        SourceFile("modOrder.bas", order_source),
    ],
    frontends=[VbaFrontend()],
)

codebase = analysis.codebase
report_diagnostics(analysis.diagnostics)

codebase.set_entries(["vba:frmOrder:bSave_Click"], kind="form_event")
context = codebase.context_for("vba:frmOrder:bSave_Click")
```

这段代码刻意体现四个决定：

1. 输入是**完整文件**，而不是调用者切好的函数片段。
2. `analyze()` 返回分析报告和可查询快照，使调用者可以先看诊断，再决定是否使用结果。
3. 入口是业务选择，不由库擅自认定。
4. 上层程序消费的是有边界的 `AnalysisContext`，而不是内部图对象。

## 核心对象

| 对象 | 调用者看到的内容 | 不负责的事 |
| --- | --- | --- |
| `SourceFile` | 路径、完整源码、可选语言提示 | 不要求调用者提供 AST、模块名或函数边界。 |
| `Function` | 稳定 ID、显示名、语言、模块、文件、源码、范围、属性 | 不暴露前端的解析树或核心索引。 |
| `Call` | 源函数、调用位置、原始调用文本、解析状态、已确定目标（如有）、证据来源 | 不把“名称相同”伪装为已确定目标。 |
| `Diagnostic` | 稳定代码、严重级别、消息、文件/函数/调用位置、可选建议 | 不只是维护者日志。 |
| `EntryPoint` | 函数、业务类别、来源 | 不将“入口”变成函数的不可变语言属性。 |
| `AnalysisContext` | 入口、可达函数与源码、内部调用、路径、诊断、截断信息 | 不生成 prompt，也不调用 LLM。 |
| `RefreshResult` | 函数和调用变化、受影响入口、诊断 | 不要求调用者理解旧新图如何比较。 |

`Function.id` 是调用者持久化引用、缓存和跨调用查询的唯一身份。它必须在同一代码库中唯一，且函数仅移动行号时保持不变。推荐形态是带语言与命名空间的字符串，例如 `vba:frmOrder:bSave_Click`、`java:OrderController#create(OrderRequest)`。

## 旅程一：建立代码库

### 调用者的目标

一次提交项目当前快照，并立刻知道哪些结果可靠、哪些文件有问题。

```python
analysis = Codebase.analyze(
    files=[
        SourceFile(path="frmOrder.frm", content=form_source),
        SourceFile(path="modOrder.bas", content=module_source),
    ],
    frontends=[VbaFrontend()],
)

for diagnostic in analysis.diagnostics:
    print(diagnostic.severity, diagnostic.code, diagnostic.message)

codebase = analysis.codebase
```

### 需要提供的能力

* `SourceFile.path` 是快照内的唯一身份；`content` 永远是完整文件；`language` 可选，用于让调用者覆盖扩展名路由。
* 一个文件必须由恰好一个 `LanguageFrontend` 接管。没有前端支持、两个前端都支持和重复路径都产生错误诊断。
* 前端可以按语言一次处理一批文件，以正确识别 VBA 跨模块调用或其他语言的跨文件语义。
* 分析可部分成功：不支持的文件和无法理解的局部源码不会抹掉其他已确认的函数和关系。
* `analysis.codebase` 始终是当前已确认事实的只读快照；调用者按诊断策略决定是否继续。严重错误的具体阻断规则由上层定义，而不是隐藏在库中。

### 为什么不直接让 `analyze()` 返回 `Codebase`

仅返回 `Codebase` 会使诊断变成容易遗漏的附属属性，也难以表达“这次建库发生了什么”。单独的分析报告把**结果**和**对结果的信心**放在同一交互点；`codebase.diagnostics` 仍可提供快照级全量诊断，方便稍后查询。

## 旅程二：查看函数和直接调用

### 调用者的目标

先确认保存事件的代码和直接调用，再反向查找一个校验函数的使用者。

```python
save = codebase.function("vba:frmOrder:bSave_Click")
for call in codebase.callees(save.id):
    print(call.source.id, "->", call.target.id)

for caller in codebase.callers("vba:modValidation:ValidateOrder"):
    print(caller.id)
```

### 需要提供的能力

* `function(id)` 返回函数；未知 ID 返回明确、可处理的“未找到”结果或领域异常，最终契约只能选一种并保持一致。
* `callees(id)` 与 `callers(id)` 默认只给已确定的**直接**关系。返回的边包含调用位置，避免同一对函数的多处调用丢失证据。
* `callees(id, transitive=True)` 与 `callers(id, transitive=True)` 给出传递关系；循环必须安全终止，结果不应把起点算作自己的依赖。
* `Function` 中的 `source` 和 `source_range` 使调用者无需再次从文件中猜测函数正文边界。
* 未解析调用可通过 `codebase.calls_from(id, resolution="unresolved")` 或上下文诊断获取；它们不混入可靠关系查询。

### 可靠性规则

图只保存“调用存在且目标已可靠确定”的直接边。函数定义、调用和诊断各自保留来源位置。对返回集合和遍历路径采用稳定排序，使报告、RAG 索引、快照测试和 CI 输出可复现。

## 旅程三：把语言线索变成业务入口

### 调用者的目标

VBA 前端可以识别 `bSave_Click` 很像表单入口，但业务方可能只关心部分窗体；未来的 Controller 或定时任务也是同理。

```python
# 接受前端提供的候选入口。
codebase.accept_entry_candidates(kind="frontend_hint")

# 追加符合业务规则的入口。
codebase.mark_entries(
    lambda function: function.attributes.get("is_controller") is True,
    kind="http",
)

# 替换为本次任务明确要求的入口。
codebase.set_entries(["vba:frmOrder:bSave_Click"], kind="form_event")
```

### 需要提供的能力

* 前端只输出 `EntryCandidate`，其中包含函数、候选类别和依据；不会自动改变业务入口集合。
* `accept_entry_candidates()` 和 `mark_entries()` 是追加操作；`set_entries()` 是替换操作；还应提供列举和移除入口的能力。
* 每个 `EntryPoint` 记录 `kind` 和 `source`（例如 `manual`、`frontend_hint`、`predicate`），便于审计“为什么这个函数会被分析”。
* 一个函数可以是多个不同业务类别的入口；入口管理不应改变函数定义或调用图。

这让语言前端专注于语法线索，调用者保留业务定义权。

## 旅程四：得到受控的入口上下文

### 调用者的目标

将“保存订单”所需代码提供给 LLM、RAG、文档或人工审阅，而不把整个仓库塞入下游系统。

```python
context = codebase.context_for(
    "vba:frmOrder:bSave_Click",
    limits=ContextLimits(
        max_depth=8,
        max_functions=80,
        max_source_chars=30_000,
    ),
)

for function in context.functions:
    index(function.id, function.source)

for path in context.paths:
    print(" -> ".join(str(item) for item in path))
```

### 需要提供的能力

* 返回入口、稳定顺序的可达函数和各自源码、上下文内已确定调用、从入口可达的路径及相关诊断。
* 支持深度、函数数量、源码字符预算、语言集合和函数谓词等明确限制。
* 达到限制时返回已包含内容与 `truncation` 说明，绝不静默丢失依赖。
* 内部循环只保留必要路径信息，不无限展开。
* 库不管理 token 估算、prompt 模板、LLM 调用、向量库或结果展示；这些是上层策略。

上下文接口是图查询的任务化封装，而非第二套图模型。调用者仍能以函数和调用 ID 回到原始快照追踪细节。

## 旅程五：刷新并定位受影响入口

### 调用者的目标

文件变更、调用被删除或文件被移除后，只对真正受影响的确认入口重新生成报告或上下文。

```python
result = codebase.refresh(
    changed_files=[
        SourceFile(path="modValidation.bas", content=changed_validation_source),
    ],
    removed_paths=["legacy/modOldValidation.bas"],
)

for entry in result.affected_entries:
    reanalyze_entry(entry)
```

### 需要提供的能力

* `changed_files` 以路径替换当前快照中的文件；新路径即新增文件。`removed_paths` 显式删除，且不能和本次更新同一路径冲突。
* `RefreshResult` 区分新增、修改、删除的函数与调用，而不是只给一个笼统布尔值。
* `affected_entries` 同时根据刷新前后反向关系计算：删除 `ValidateOrder` 或删除 `SaveOrder -> ValidateOrder` 时，过去依赖它的保存入口仍会被报告。
* 刷新返回新的可靠快照，或以清晰的原子性语义更新原快照；最终 API 必须二选一。设计倾向于返回新快照，以便调用者保留旧结果做比较。
* 第一版可重新分析全量文件，优先保证跨文件和跨语言语义一致；未来的缓存或增量策略不可改变可见结果。

## 旅程六：多语言与跨语言调用

### 调用者的目标

将 VBA、Java、PL/SQL 等文件放在同一个逻辑代码库中，同时不因同名函数而得到错误关系。

```python
analysis = Codebase.analyze(
    files=all_files,
    frontends=[VbaFrontend(), JavaFrontend(), PlSqlFrontend()],
    linkers=[HttpRouteLinker(), RpcContractLinker()],
)
```

### 两个对照场景

**可链接**：VBA 的调用被前端识别为请求 `POST /orders`；Java 前端识别 `OrderController.create()` 处理相同且完整的路由契约。`HttpRouteLinker` 能记录路由、HTTP 方法和契约来源，因此可建立从 VBA 调用到 Java 函数的边，并把证据标注在 `Call` 上。

**不可链接**：VBA 出现 `CreateOrder()`，Java 和 PL/SQL 中各有同名过程，但没有类型、路由、IDL、RPC 合约或其他可审计关联。此调用保持未解析，诊断说明候选不唯一；绝不能自动连接其中之一。

### 需要提供的能力

* `LanguageFrontend` 负责语言内部的文件支持、函数发现、源码范围、语言语义下的解析、候选入口和诊断。
* 所有前端完成后，可选 `CrossLanguageLinker` 处理互操作协议。它与前端一样只能输出标准的函数、调用更新和诊断，不能修改核心内部索引。
* `Call` 的解析证据可让调用者区分同语言静态解析、路由契约、IDL、人工映射等来源。
* 没有链接器不妨碍单语言分析；链接器失败也不应破坏已确认的本地调用关系。

## 诊断：库必须交给调用者的控制面

诊断不是日志。每条 `Diagnostic` 至少包括：

| 字段 | 用途 |
| --- | --- |
| `code` | 稳定、机器可读的分类，如 `unsupported_file`、`ambiguous_frontend`、`duplicate_function_id`、`unresolved_call`。 |
| `severity` | 让上层区分信息、警告和错误，并制定是否阻断的策略。 |
| `message` | 适合人类阅读的上下文说明。 |
| `location` | 可选的文件、函数、行列或调用位置。 |
| `related_locations` | 冲突定义、候选目标或协议声明等关联证据。 |
| `suggestion` | 可选的调用者下一步，例如注册前端或提供映射。 |

核心原则是“未确定即明确未确定”。诊断可以让调用者选择容忍、展示、统计或阻断，但库不会用猜测填补调用图。

## 对维护者的最小扩展边界

普通调用者只使用 `Codebase` 和上述结果模型。维护者只有在增加语言或互操作协议时才接触扩展接口：

```python
class LanguageFrontend(Protocol):
    def supports(self, file: SourceFile) -> bool: ...
    def analyze(self, files: Sequence[SourceFile]) -> FileAnalysis: ...

class CrossLanguageLinker(Protocol):
    def link(self, analysis: LinkableAnalysis) -> LinkResult: ...
```

这些签名仅表达分层意图。正式契约需要在以下问题达成一致后再固定：

* 文件分派与批处理发生在何处，如何报告“零个或多个前端支持”；
* `FileAnalysis` 如何表达已解析调用、未解析调用、入口候选和诊断；
* 链接器获得哪些不可变事实，如何附加目标与证据；
* 前端与链接器的失败是否仅产生诊断，何时导致分析没有可用快照；
* 怎样用共享 fixture 验证稳定 ID、范围、调用归属、确定性及刷新影响。

`Codebase` 仅负责收集全局函数、校验唯一性、建立正反向直接边索引、管理入口、生成上下文和计算刷新影响。语言大小写、重载、Package、VBA 事件、路由和 RPC 协议必须留在相应前端或链接器中。

## 尚待评审的决定

1. `analyze()` 和 `refresh()` 是返回新快照，还是原地更新对象；本文倾向前者以利于比较和并发使用。
2. 查询未知 ID 应返回 `None`/结果对象，还是抛出一个小而明确的领域异常；同一 API 内不能混用。
3. 上下文预算的基本单位是源码字符、字节、近似 token，还是由调用者提供预算器；首版最可预测的选项是字符数。
4. “手工映射”是否是标准跨语言链接证据；若支持，映射需要携带谁提供、为什么可信和适用范围。
5. 一个快照是否允许在存在错误诊断时暴露部分结果；本文建议允许，并由调用者依据严重级别决定是否采用。

这些决定确认后，才应将内容收敛为正式 `spec.md`、更新 `usage.md`，并为前端与维护者编写契约文档。
