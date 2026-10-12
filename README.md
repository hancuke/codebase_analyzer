# Code documentation workspace

此 uv workspace 将源代码变化转换为可组合的文档 fragment 更新。它不规定一个 Entry 必须生成一份
文件，也不将 LLM、Markdown 编辑和文件发布耦合在同一个包中。

```text
FileTracker ChangeSet + complete source snapshot
  -> change_analyzer.analyze_changes()
  -> change_analyzer.entry_changes()
  -> caller-generated DocumentResult
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
| `document_updater/` | 以纯函数方式读取和应用受管 Markdown fragment 的 desired state。 |

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

### Local PL/SQL analysis workload

独立函数 `assess_plsql_complexity(source)` 接受单个完整 PL/SQL function/procedure 的源码，
估算小模型解释业务所需的分析工作量，建议单轮或多轮分析。不调用 LLM、不追踪被调用函数。

```python
from codegraph import PlsqlAnalysisMode, assess_plsql_complexity

assessment = assess_plsql_complexity(function.source)
mode = assessment.mode  # SINGLE_PASS / MULTI_PASS
prompt_kind = "multi_pass" if mode is PlsqlAnalysisMode.MULTI_PASS else "single_pass"
logical_length = assessment.metrics.logical_units
for item in assessment.evidence:
    print(item.reason.value, item.points)
# Caller owns prompt selection, LLM execution and multi-pass synthesis.
```

结果是 frozen `PlsqlComplexityAssessment`，包含枚举 `mode`、`score`、
frozen `PlsqlWorkloadMetrics` 和不可变 `evidence`，不返回 diagnostics。
计分证据用 `PlsqlWorkloadReason` 表达原因，`points` 表达贡献；结构上限以负贡献体现，
短源码保护以零分原因体现。证据按固定顺序返回。

有效逻辑长度 E 和调整后的代码行数 A 共同表达长度：普通执行语句、分支和循环头各计 1，
简单直接赋值和声明计 0.25，NULL 和块结束符不计。
普通 DML 或初始查询块计 1，宽表字段和值列表不按字段数累加。
JOIN、额外查询块、集合运算、GROUP BY/HAVING、聚合和窗口表达式各加 1；
非直接映射的 SET/VALUES/SELECT 表达式按逻辑表达式计量。
复杂表达式按每 20 个词法 token 向上取整，已计量的 CASE/查询/聚合/窗口表达式不重复计。
参数/返回类型签名和块分隔符不增加 E；Oracle CASE 的 WHEN 计为决策分支。

```text
结构原始分 = 2 × 决策数 + 循环数 + 3 × 嵌套控制单位
           + 2 × SQL 结构数 + 游标声明数 + 不同 FETCH 游标数 + 异常处理分支数
R = min(30, 结构原始分)
P = min(10, 非空非注释物理代码行数 / 50)
score = max(E, A) + R + P
```

嵌套控制单位为每个 IF/CASE/LOOP 开始时 `max(0, 控制深度 - 1)` 的总和，
普通 BEGIN 块不增加控制深度。SQL 结构数不包含已经计为决策的 CASE。
INSERT SELECT 和 MERGE 中的 DML 不重复算顶层语句。
注释、字符串（含 Oracle q quoting）和带引号标识符不参与关键词匹配。
调整后的代码行数 A 从非空非注释代码行数中扣除普通 DML 的字段和值映射续行；
含 CASE、SELECT、非直接映射或 SQL 结构的语句不压缩，WHERE 等后续条件保留。
同一行还有其他代码时不扣除。E 不受排版影响，A 和最终分数会受排版影响。
完整物理行数仍用于弱修正 P；包含多行字面量的代码行仍计入物理长度。

只有 **物理代码行数 <= 50 且 E <= 50** 才受单轮保护；其他源码 **score >= 100 建议 MULTI_PASS**，
否则仍为 SINGLE_PASS。因此，一个短循环或 200 行的宽表 INSERT 不会直接触发多轮，
但 100 个普通执行步骤即使压在一行也会升级。
50 同时限制实际代码行数和逻辑单位。100 仍是初始工作量阈值，并非小模型能力保证：
124 行且有多阶段校验、查询及异常分支的示例现为 156.48 分，建议多轮，
不会再因 E=46.75 被短函数保护。需用实际模型结果继续校准阈值。

单轮/多轮仅由 E 和 score 决定，不因动态 SQL、局部子程序或控制结构异常强制升级。
动态 SQL 按普通代码计量，不分析字符串中的 SQL。输入应为完整源码；此函数不提供语法诊断，
也不验证完整 PL/SQL 语法。未闭合的字符串、带引号标识符或注释会抛出 `ValueError`，
避免返回截断源码的分数。此启发式不推断真实业务语义，需用实际函数和模型结果校准。

### Context to one document result

```python
from document_updater import DocumentResult, ResultKind

result = DocumentResult(
    fragment_id="entry:vba:frmOrder:bSave_Click",
    kind=ResultKind.UPSERT,
    markdown=generated_markdown,
)
```

调用方拥有 prompt、LLM client 和 Markdown 合并策略，并生成一个
`DocumentResult(kind=ResultKind.UPSERT, ...)`。删除的内容由调用方直接构造
`DocumentResult(fragment_id, ResultKind.DELETE)`，无需调用 LLM。

### One result to one Markdown document

```python
from document_updater import apply_result

updated_markdown = apply_result(existing_markdown, result)
```

受管内容采用 `codegraph:fragment` markers。UPSERT 在缺失时插入、存在时替换；DELETE 幂等删除。
物理文件是否应删除由调用方决定。

调用方拥有 fragment 的来源、`fragment_id -> document key` 映射、分组、Markdown 加载/发布、审核队列
与 FileTracker baseline 推进。支持一 Entry 一 fragment、多个 Entry 合成一个 fragment，或多个
fragment 合并到一个文档。

调用方可在读取目标 fragment 后构造不同 prompt：首次新增只发送当前 bounded code context，避免和
`code_change`/diff 重复；更新才发送现有 fragment、当前代码和受限的变更摘要。需要小模型多角度
分析时，调用方为同一 Entry 顺序执行 1..N 组 system/user prompts，并合并为唯一的
`DocumentResult`，不会产生多份互相覆盖的 Entry fragment。UI 控件表格等确定性内容由独立
producer 生成自己的 `DocumentResult`，可与 Entry fragment 共享同一 caller-owned document key。

## Runnable examples

```bash
uv run python examples/initial_documentation.py
uv run python examples/changed_module_documentation.py
```

两个独立脚本各自在临时目录中演示一个调用方场景：

1. [`initial_documentation.py`](examples/initial_documentation.py)：创建 VBA 源码后调用 `tracker.scan()`，从全部 Entry 生成文档；所有文档发布成功后，由调用方创建 baseline。
2. [`changed_module_documentation.py`](examples/changed_module_documentation.py)：先生成初始文档并建立 source baseline，再修改一个被 Entry 调用的 module；再次调用 `tracker.scan()` 后，`entry_changes()` 只返回受影响 Entry，发布更新后由调用方推进 baseline。

示例约定 VBA 导出文件同级存放，并使用 `Form_*.txt` 和 `Module_*.txt` 命名。每个 Form 的完整
分析上下文是该 Form 加上全部 Module；`.txt` 文件由示例适配器显式标记为 VBA。Form 按
UTF-16 读取，并只将 `CodeBehindForm` 行之后的 VBA 代码交给 CodeGraph；Module 支持 UTF-8
或带 BOM 的 UTF-16。

公共的加载、Form 范围分析、文档生成与发布对象位于
[`documentation_example_support.py`](examples/documentation_example_support.py)。
两个脚本都明确展示 `scan() -> affected_entries_by_form()
-> publish_documents_for_forms() -> commit()`：每个 Form 是一个文档的所有者，
其受影响 Entry 只更新该 Form 的文档；baseline 只在所有文档成功发布后由调用方推进。

[`resumable_documentation.py`](examples/resumable_documentation.py) 提供可恢复的
`run_documentation(source_root, document_root, llm, *, generation_version="demo-v1")`。
它先把完整最终 Markdown 和摘要记录暂存到 `document_root/.resumable/`，有效结果在重启时跳过
分析和 LLM 并打印日志。全部生成成功后才逐文件发布，随后提交 baseline 并删除暂存目录。
生成失败不会修改正式文档；发布失败可能留下部分更新，但重试不必重新生成有效结果。
此接口不再接受独立的 `progress_path`，也不迁移旧 `progress.json`。布局、失效规则和恢复边界见
[`文档生命周期`](docs/document-lifecycle.md#可恢复的暂存与发布)。

## Setup and validation

Python 3.10+ 与 uv 是必需条件。

```bash
uv sync --all-packages
uv run pytest code_graph/tests file_tracker/tests change_analyzer/tests document_updater/tests
uv run pytest examples/test_resumable_documentation.py examples/test_progress_checkpoint.py examples/test_documentation_example_support.py
uv build --all-packages
```

领域边界与完整生命周期见：

- [`docs/document-domain-model.md`](docs/document-domain-model.md)
- [`docs/document-lifecycle.md`](docs/document-lifecycle.md)
- [`docs/mvp-walkthrough.md`](docs/mvp-walkthrough.md)
