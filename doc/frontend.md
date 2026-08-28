# 编写 CodeGraph 语言 Frontend

一个语言 Frontend 将某种语言的完整源码文件转换为 CodeGraph 的统一事实：函数、调用、候选入口和诊断。它负责“读懂语言”，核心库负责“验证、索引、查询和刷新”。

为了降低 Frontend 的接入门槛，CodeGraph 提供了内置的 **`BaseFrontend` 模板基类**。它将复杂的 5 步流水线（文件解析 -> 符号表构建 -> 调用点抽取 -> 符号匹配建边 -> 诊断与结果打包）封装在基类中。新语言接入时**无需重写流程算法**，只需实现 2 个极简的语法 Hook！

---

## 推荐方式：继承 `BaseFrontend` 模板基类

### 为什么使用 `BaseFrontend`？

如果不使用模板，每个 Frontend 开发者都需要重复编写以下复杂算法逻辑：
1. 遍历文件，收集所有函数与语法错误。
2. 按语言名称归一化规则（如 VBA 不区分大小写，Python 区分大小写）建立两阶段符号表。
3. 遍历函数，识别调用词。
4. 进行符号匹配判断：
   - 匹配到 **1 个**目标：成功建边并附带证据。
   - 匹配到 **0 个**或 **多个**目标：自动转为未解析调用 (`Call.target_id = None`)，并生成格式化的 `unresolved_call` 警告诊断。
5. 组装不可变的 `FileAnalysis` 对象。

继承 `BaseFrontend` 后，上述算法全部由基类自动完成！

### 极简接入示例（如 Python Frontend）

```python
from codegraph import BaseFrontend, Function, Diagnostic, RawCall, SourceFile, SourceRange

class PythonFrontend(BaseFrontend):
    # 1. 声明语言参数
    language_name = "python"
    file_extensions = {".py"}
    is_case_sensitive = True

    # 2. 必选 Hook A：怎么从单个文件抽取函数
    def extract_functions(
        self, source_file: SourceFile
    ) -> tuple[list[Function], list[Diagnostic]]:
        # 用 ast / 正则提取函数名、范围与源码正文
        functions = [...]
        diagnostics = [...]
        return functions, diagnostics

    # 3. 必选 Hook B：怎么从函数抽取原始调用点 (未解析)
    def extract_raw_calls(self, function: Function) -> list[RawCall]:
        #         只需返回调用的 (名称, 行号)
        return [
                    RawCall(name="calculate_total", line=15),
        ]

    # 4. 可选 Hook C：如何判定候选入口（例如 FastAPI 路由或 main 函数）
    def detect_entry_candidate(self, function: Function):
        if function.name == "main" or function.attributes.get("is_route"):
            return EntryCandidate(function_id=function.id, kind="entry_point")
        return None
```

这就是写一个新语言 Frontend 的全部工作！

---

## 底层契约协议：`LanguageFrontend` (Protocol)

如果你不想使用基类，或者有极其特殊的分析管线（例如整合了外部编译器的 LSP），也可以直接实现原始 Protocol：

```python
class LanguageFrontend(Protocol):
    def supports(self, file: SourceFile) -> bool:
        """Return whether this frontend owns the file."""

    def analyze(self, files: Sequence[SourceFile]) -> FileAnalysis:
        """Analyze a batch of files supported by this frontend."""
```

在调用 `Codebase.analyze(files, frontends)` 时传入你的实例即可。

---

## `BaseFrontend` 5 步流水线（Pipeline）原理

```text
files: SourceFile[]
    |
    v (Step 1: extract_functions)
Functions[] + Diagnostics[]
    |
    v (Step 2: normalize_name & build symbol_index)
SymbolIndex: { normalized_name -> Function[] }
    |
    v (Step 3: extract_raw_calls & Step 5: detect_entry_candidate)
RawCalls[] & EntryCandidates[]
    |
    v (Step 4: resolve_target & _resolve_and_diagnose)
1 个匹配: Call(target_id=...)
0 / 多匹配: Call(target_id=None) + Diagnostic(code="unresolved_call")
    |
    v
FileAnalysis(functions, calls, entry_candidates, diagnostics)
```

### 可重写的扩展 Hook

`BaseFrontend` 提供了丰富的可重写 Hook 点：

| Hook 方法 / 配置项 | 默认行为 | 用途 / 覆盖场景 |
| --- | --- | --- |
| `language_name` | `""` | 语言标识，如 `"python"`, `"java"`。用于诊断信息和证据标注。 |
| `file_extensions` | `set()` | 支持的文件后缀名，如 `{".py"}` 或 `{".bas", ".cls", ".frm"}`。 |
| `is_case_sensitive` | `True` | 名字是否区分大小写。VBA/SQL 可设为 `False`。 |
| `extract_functions(file)` | **必选** | 从单个文件中解析出 `Function` 列表与文件级语法 `Diagnostic`。 |
| `extract_raw_calls(function)` | **必选** | 从函数源码中提取原始调用点 `RawCall(name, line)`。 |
| `detect_entry_candidate(func)` | 返回 `None` | 判断函数是否是候选入口。 |
| `resolve_target(raw_call, func, index)` | 单名精准匹配 | 覆盖默认的符号匹配逻辑（如处理类方法限定名、作用域或重载）。  
 

### 规则


* `file.language` 非空且不属于该语言时，返回 `False`。不要为了“尽量分析”而抢占显式标记为其他语言的文件。
* 扩展名仅是没有语言提示时的兜底规则。一个 Frontend 不能声称支持所有未知扩展名。
* 不要在此方法抛出“无法解析”的异常；语法问题属于 `analyze()` 的诊断。
* 不要在此方法读取 `file.content` 以进行复杂启发式判断。若格式相同而语言不同，应要求调用者显式设置 `language`，以避免多个 Frontend 竞争。

`Codebase` 会对零个或多个支持者分别产生 `unsupported_file` 或 `ambiguous_frontend` 错误诊断，并且不会调用任何竞争 Frontend 的 `analyze()`。

## 方法二：`analyze`

```python
def analyze(self, files: Sequence[SourceFile]) -> FileAnalysis:
    ...
```

### 输入契约

* `files` 中每个文件都已由当前 Frontend 的 `supports()` 接受。
* 调用者传入的是完整源码；不能要求调用者先按函数切分。
* 文件顺序没有语义。实现应按稳定路径排序，或确保结果不依赖输入顺序。
* 批次可能为空；建议返回空的 `FileAnalysis()`。
* 当前 MVP 不提供文件系统、项目配置、网络或全局 `Codebase` 给 Frontend。需要的所有源码都应来自 `files`。

### 输出契约

`analyze()` 返回一个 `FileAnalysis`，四类结果均使用不可变 tuple：

```python
FileAnalysis(
    functions=tuple[Function, ...],
    calls=tuple[Call, ...],
    entry_candidates=tuple[EntryCandidate, ...],
    diagnostics=tuple[Diagnostic, ...],
)
```

局部解析失败不应让 Frontend 丢弃其他文件的结果。能确认的函数和调用照常返回；无法理解或无法确定的部分应返回诊断。

## 产出函数：`Function`

```python
Function(
    id="python:orders.service:submit_order",
    name="submit_order",
    language="python",
    module="orders.service",
    file="src/orders/service.py",
    source="def submit_order(...):\n    ...\n",
    source_range=SourceRange(start_line=12, end_line=24),
    attributes={"visibility": "public"},
)
```

Frontend 必须保证：

1. **稳定 ID**：函数仅移动行号、空白或不影响身份的注释时，ID 不变。ID 不可由行号或字符偏移构成。
2. **全局唯一性**：所有语言共同构成一个代码库，ID 必须携带足够的语言和命名空间信息。推荐前缀为小写语言标识，例如 `python:`、`java:`、`plsql:`。
3. **准确可追溯**：`file` 必须等于输入 `SourceFile.path`；`source` 是包含声明和结尾的原始片段；行号采用从 1 开始的闭区间。
4. **语言规则局部化**：大小写、重载、包、类、命名空间和嵌套函数由 Frontend 编码进 ID 与 `module`。核心不会替你处理这些语义。

如果无法生成可靠 ID 或函数范围，宁可不产出该函数并给出诊断，也不要生成会在刷新后漂移的 ID。

## 产出调用：`Call`

```python
Call(
    source_id="python:orders.service:submit_order",
    name="validate_order",
    line=16,
    target_id="python:orders.validation:validate_order",
    evidence="python_import_and_scope",
)
```

每个实际调用位置对应一个 `Call`，即使同一源函数多次调用同一目标也应保留多条记录。字段含义如下：

| 字段 | 要求 |
| --- | --- |
| `source_id` | 必须是同次 `FileAnalysis` 产出的函数 ID。 |
| `name` | 源码中调用者可识别的原始/显示名称。 |
| `line` | 1 开始的调用所在行。 |
| `target_id` | 只有目标可被该语言语义**可靠确定**时才填写。 |
| `evidence` | 填写解析依据，如 `java_type_resolution`、`python_import_and_scope`，方便审计。 |

不要通过简单同名匹配建立调用边。对于重载方法、动态派发、宏、反射、未导入名称或多个可见候选，保留 `target_id=None`，并输出 `unresolved_call` 警告。核心只把 `target_id` 存在且属于当前代码库的调用写进图；无效目标会得到 `missing_call_target` 警告。

前端能够确定的调用可以引用同一批次稍后声明的函数：先收集完整函数表，再解析所有调用。VBA Frontend 的实现即采用这个两阶段方式。

## 产出候选入口：`EntryCandidate`

```python
EntryCandidate(
    function_id="java:orders.OrderController#create(OrderRequest)",
    kind="http_handler",
)
```

候选入口是语言/框架可观察到的线索，而非最终业务决定。Controller 路由、CLI `main`、测试入口、定时任务或消息消费者可以产出候选；普通函数不应因为名称猜测被标记。

`Codebase` 不会自动把候选入口加入当前入口。调用者随后通过 `accept_entry_candidates()`、`mark_entries()` 或 `set_entries()` 确认它们。

## 产出诊断：`Diagnostic`

使用诊断报告预期内的源码问题，避免将它们隐藏或仅输出日志：

```python
Diagnostic(
    code="unresolved_call",
    severity="warning",
    message="Cannot resolve call 'dispatch': receiver type is dynamic.",
    path="src/orders/service.py",
    function_id="python:orders.service:submit_order",
    line=16,
)
```

推荐诊断代码：

| 情形 | 代码 | 严重级别 |
| --- | --- | --- |
| 文件语法不足以安全恢复 | `parse_error` | `error` |
| 函数没有闭合或范围不可靠 | `unterminated_procedure` | `error` |
| 找到调用但目标不存在或有多个候选 | `unresolved_call` | `warning` |
| 语言结构被部分支持，结果可能不完整 | `unsupported_construct` | `warning` |
| 仅提供补充信息 | 自定义稳定代码 | `info` |

诊断应包含文件、函数或调用位置中尽可能精确的定位。不要吞掉编程错误：非预期异常应带着上下文明确失败，而不是返回貌似成功的空分析。

## 一个最小 Frontend 模板

以下示例展示实现顺序；`_extract_functions` 和 `_find_calls` 应替换为目标语言的解析器、编译器 API、LSP 或可靠文本解析。

```python
from pathlib import PurePath
from typing import Sequence

from codegraph import (
    Call,
    Diagnostic,
    FileAnalysis,
    Function,
    SourceFile,
    SourceRange,
)


class ExampleFrontend:
    language = "example"

    def supports(self, file: SourceFile) -> bool:
        return file.language == self.language or (
            file.language is None
            and PurePath(file.path).suffix.casefold() == ".example"
        )

    def analyze(self, files: Sequence[SourceFile]) -> FileAnalysis:
        functions: list[Function] = []
        diagnostics: list[Diagnostic] = []

        for file in sorted(files, key=lambda item: item.path):
            discovered, file_diagnostics = self._extract_functions(file)
            functions.extend(discovered)
            diagnostics.extend(file_diagnostics)

        # 所有函数已发现后，才可处理前向和跨文件引用。
        symbols = self._build_language_symbol_table(functions)
        calls: list[Call] = []
        for function in functions:
            for name, line in self._find_calls(function):
                target = self._resolve(name, function, symbols)
                if target is None:
                    diagnostics.append(
                        Diagnostic(
                            code="unresolved_call",
                            severity="warning",
                            message=f"Cannot reliably resolve {name!r}.",
                            path=function.file,
                            function_id=function.id,
                            line=line,
                        )
                    )
                calls.append(
                    Call(
                        source_id=function.id,
                        name=name,
                        line=line,
                        target_id=target.id if target else None,
                        evidence="example_semantic_resolution" if target else None,
                    )
                )

        return FileAnalysis(
            functions=tuple(functions),
            calls=tuple(calls),
            diagnostics=tuple(diagnostics),
        )

    def _extract_functions(
        self, file: SourceFile
    ) -> tuple[list[Function], list[Diagnostic]]:
        # 用语言解析器提取声明、原始源码、范围和稳定 ID。
        raise NotImplementedError

    def _build_language_symbol_table(self, functions: Sequence[Function]) -> object:
        raise NotImplementedError

    def _find_calls(self, function: Function) -> list[tuple[str, int, int]]:
        raise NotImplementedError

    def _resolve(
        self, name: str, source: Function, symbols: object
    ) -> Function | None:
        raise NotImplementedError
```

## 不属于当前 `LanguageFrontend` 的职责

* 从文件系统递归发现源文件；
* 更新 `Codebase` 内部索引，或管理入口集合；
* 将未确定调用强行补成图边；
* 根据 HTTP、RPC、消息主题或人工映射连接其他语言；
* 生成 LLM prompt、报告或可视化。

跨语言协议链接需要读取所有语言的聚合结果，属于后续 `CrossLanguageLinker` 扩展点，而不是单个 Frontend 的猜测逻辑。

## 实现完成前的检查清单

1. `supports()` 对显式语言提示和扩展名路由是确定且互斥的。
2. `analyze()` 在单文件、跨文件、前向声明和不同输入顺序下都返回相同事实。
3. 所有函数 ID 稳定、语言限定且全局唯一。
4. 每个函数都有正确原始源码和 1 开始、闭区间的范围。
5. 每条调用都有正确来源和位置；仅可靠目标填写 `target_id`。
6. 未解析和部分解析都作为诊断出现，不生成猜测边。
7. 语言特有规则不泄漏到 `Codebase` 或其他 Frontend。
8. 使用该 Frontend 的 `Codebase.analyze()` 能处理诊断、查询、入口和刷新，不需要任何核心特例。

可参考当前实现的 [`VbaFrontend`](../src/codegraph/vba.py)，以及调用者视角的 [`usage.md`](usage.md) 和整体边界说明 [`architecture.md`](architecture.md)。
