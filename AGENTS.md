# Agent Engineering Guide

本文件适用于整个 workspace。模型在分析、设计、编码、测试和文档更新时都必须遵循这些约束。
若子目录存在更具体的 `AGENTS.md`，则子目录规则在其范围内优先，但不得破坏这里定义的架构边界。

## 1. 核心原则：先做 Domain Modeling，再写代码

除纯文案、拼写或机械格式修改外，**不得直接开始编写实现代码**。开始修改前，先完成与任务规模相称的
领域建模，并明确以下内容：

1. **业务目标**：要改变哪个可观察行为，什么结果算完成。
2. **领域概念**：涉及哪些实体、值对象、枚举、状态、事件或策略；使用仓库已有术语，不创建同义概念。
3. **不变量**：哪些条件在对象创建后、流程执行中和输出返回前必须始终成立。
4. **所有权**：哪个模块拥有数据和规则，哪个模块只负责协调，谁可以修改状态。
5. **接口边界**：输入、输出、错误、diagnostic、不可变性和排序保证是什么。
6. **依赖方向**：调用从哪里流向哪里；确认没有为了复用而形成反向依赖或循环依赖。
7. **失败模型**：哪些是预期领域问题，哪些是调用方错误，哪些异常必须传播。
8. **验证方式**：用哪些测试证明正常路径、边界条件和不变量。

建模结果不要求创建额外设计文件，但必须先在现有模型、接口和测试中找到落点。若一个新概念无法明确
归属，先调整设计，不要把它塞进最方便修改的模块。

### 编码前检查

在写生产代码前，必须能够回答：

- 这是新领域概念，还是已有概念的新行为？
- 规则应位于领域模型、领域服务、适配器还是编排层？
- 是否已有可复用的类型、协议、诊断结构或稳定标识？
- 数据是否需要跨包传递？若需要，最小稳定接口是什么？
- 是否会把文件系统、Git、LLM、序列化或语言细节泄漏到不应知道它们的包？
- 是否保持不可变快照、确定性排序和可追溯来源？

如果答案不明确，继续阅读模型、公共 API 和相关测试，不要先写代码再反推设计。

## 2. Workspace 领域与依赖方向

本项目是一个 `uv` workspace，包含四个职责单一的包：

| Package | 领域职责 | 不应承担的职责 |
| --- | --- | --- |
| `code_graph/` | 从完整源码快照提取函数、调用、入口和 diagnostics；验证并查询依赖图 | 文件系统遍历、Git/change tracking、文档更新、LLM 调用 |
| `file_tracker/` | 跟踪物理文件变化、baseline transaction、内容快照和 revision 校验 | 解析语言语法、理解函数调用图、决定入口影响 |
| `change_analyzer/` | 组合旧/新快照与 CodeGraph，计算函数变化、调用边变化和入口影响 | 修改 baseline、发布文档、执行 LLM |
| `document_updater/` | 提供文档 authoring、确定性文档计划和 Markdown fragment mutation | 重新分析源码、推断代码依赖、拥有文件变更事实 |

允许的高层依赖方向：

```text
file_tracker       code_graph
      \             /
       change_analyzer
              |
       document_updater
```

必须保持依赖单向：

- `code_graph` 与 `file_tracker` 是相互独立的基础领域。
- `change_analyzer` 可以组合二者，但基础领域不得反向依赖它。
- `document_updater` 可以消费 change-analysis 结果，但其他包不得依赖文档或 LLM 模型。
- 跨包共享行为应通过最小公共类型或协议表达，不得通过导入内部实现来绕过边界。

## 3. 模块和接口设计规则

### 3.1 模型负责表达不变量

- 稳定领域对象优先使用 frozen dataclass、tuple 和只读 mapping。
- 在对象构造时即可验证的约束放入模型，不要分散到多个调用点。
- 不要引入可变全局状态、隐式缓存或依赖执行顺序的快照。
- 新枚举或状态必须定义合法转换及非法组合的处理方式。
- 不要用松散的 `dict[str, object]` 替代已明确、会跨边界传递的领域结构。

### 3.2 服务负责领域流程，适配器负责 I/O

- 纯比较、图遍历、计划生成和映射逻辑应保持确定性并尽量无 I/O。
- 文件系统路径映射、读写、Git、CLI 和外部 provider 属于适配器层。
- 编排函数可以连接多个领域服务，但不应复制下层领域规则。
- 不为单个调用点增加无意义抽象；只有存在清晰边界、替换需求或可独立验证的不变量时才提取接口。

### 3.3 公共接口必须明确

新增或修改公共 API 时，明确并测试：

- 接受的是完整快照还是增量 diff；
- 标识符的规范化规则和稳定性；
- 返回集合的顺序；
- unresolved、unsupported、ambiguous 等预期问题如何表示；
- 未找到对象时返回 `None`、领域 diagnostic 还是抛出特定异常；
- 输入和输出由谁拥有，调用方能否修改。

优先扩展现有 facade 和协议。不要让调用方直接构造会绕过验证的核心对象，也不要暴露内部索引或 AST。

## 4. CodeGraph 特定不变量

- 使用 `Codebase.analyze(files, analyzers)` 作为受支持的构建入口；不要绕过聚合和验证流程。
- `SourceFile.source_id` 是规范化的项目相对 POSIX 逻辑 ID，不是物理 `Path`：
  - 使用 `/`；
  - 不含前导 `/`、盘符、workspace 根路径、`.` 或 `..` 段。
- 输入源码是完整快照，不是 diff。
- `Function.id` 必须稳定且唯一，不得依赖行号或字符偏移。
- 每个输入文件必须恰好由一个 analyzer 支持；零个或多个匹配都生成结构化 diagnostic。
- 必须先收集全部函数，再解析调用，以支持前向和跨文件引用。
- 只有可靠且具有有效 `target_id` 的调用才能成为图边。
- unresolved call 必须保留在查询结果中并附带 diagnostic，不能静默丢弃。
- `SourceRange` 使用从 1 开始的闭区间行号。
- 图遍历必须处理环，且不得把起始函数作为自己的依赖返回。
- 函数、调用、入口、diagnostics、路径和查询结果必须保持确定性顺序。

语言特有的解析、名称解析、入口规则和 diagnostics 必须留在对应 analyzer 中。新增 analyzer 通常继承
`BaseAnalyzer`，实现 `language_name`、`file_extensions`、`extract_functions()` 和
`extract_raw_calls()`，仅在语言规则确有需要时覆盖解析或入口检测。

## 5. Change analysis 与文档领域规则

- 入口影响必须取 baseline 和 working 两张图的反向可达结果并集，不能只看新图。
- 变化函数与入口是多对多关系；共享变化不能被强制归属到单一入口。
- 必须保留 old/new path evidence。
- 无可达入口的变化保留在 `unassigned_changes`，不能静默忽略。
- 文档计划以单个入口为边界，LLM 不应从 repository-wide diff 猜测入口归属。
- 文档规划与 reference data 必须确定性生成。
- 文档发布成功前不得推进 FileTracker baseline；baseline commit 是流程的最后一步。

## 6. 错误和 Diagnostic 约定

- 预期的解析、路由和名称解析问题使用结构化 `Diagnostic`。
- 未知 source/function 查询使用现有特定异常或既定的 `None` 契约，不创建含糊的通用异常。
- 意外实现错误必须传播，不能用宽泛 `try/except` 转成“成功”或空结果。
- 不得静默跳过无效输入。应按所属领域的既有方式返回 diagnostic、validation error 或明确异常。
- 新错误码、severity 或状态必须稳定、可测试，并与现有命名风格一致。

## 7. 实施工作流

1. 阅读相关模型、公共入口、调用方和测试，确认当前领域语言。
2. 写出最小领域设计：概念、不变量、所有权、接口、失败模型和依赖方向。
3. 搜索已有实现并复用；避免重复 helper、类型和规范化规则。
4. 先修改或补充领域模型与接口，再实现服务流程和适配器。
5. 为行为变化添加测试，覆盖正常路径、边界、不变量、错误以及确定性排序。
6. 运行最小相关测试；涉及跨包契约时运行所有受影响包测试。
7. 检查 diff，确认没有越界职责、反向依赖、可变状态或无关改动。

不要为“以后可能需要”增加字段、层级或扩展点。以当前任务需要的最小完整模型为准，同时确保接口不会
泄漏实现细节。

## 8. 测试与构建

在 workspace 根目录使用现有工具：

```bash
uv sync --all-packages
uv run pytest code_graph/tests file_tracker/tests change_analyzer/tests document_updater/tests
uv build --all-packages
```

优先运行覆盖改动的最小测试。例如：

```bash
uv run pytest code_graph/tests/test_codebase.py::test_vba_analysis_resolves_cross_file_and_forward_calls
uv run pytest change_analyzer/tests/test_change_analyzer.py
uv run pytest document_updater/tests/test_planner.py
```

项目没有配置 lint 工具，不要擅自引入新的 lint、format 或 build 工具。修改公共模型、路由规则、图查询、
analyzer contract 或跨包数据结构时，必须同步更新相关测试和示例。

## 9. 完成标准

只有同时满足以下条件，任务才算完成：

- 实现符合事先确定的领域模型和模块所有权；
- 公共接口边界清晰，依赖方向未被破坏；
- 不变量、错误语义、不可变性和确定性得到保留；
- 相关测试通过，必要时 workspace build 通过；
- 文档和示例与实际行为一致；
- diff 中没有无关重构或跨领域泄漏。

架构和领域背景优先参考：

- `README.md`
- `docs/document-domain-model.md`
- `docs/document-lifecycle.md`
- `docs/mvp-walkthrough.md`
- 各包的 `models.py`、公共 `__init__.py` 和测试
