# Code documentation workspace

此 uv workspace 将源代码变化转换为可组合的文档 fragment 更新。它不规定一个 Entry 必须生成一份
文件，也不将 LLM、Markdown 编辑和文件发布耦合在同一个包中。

```text
FileTracker ChangeSet + complete source snapshot
  -> change_analyzer.analyze_changes()
  -> change_analyzer.entry_changes()
  -> language caller prepares document tasks
  -> document_workbench skips document_keys with saved results
  -> caller generates and combines document fragments
  -> document_workbench stores completed document results
  -> caller publishes results
```

## Workspace packages

| Package | Responsibility |
| --- | --- |
| `code_graph/` | 从完整源码快照提取函数、调用、入口、diagnostics，并提供确定性图查询。 |
| `file_tracker/` | 跟踪物理文件变化并维护 immutable baseline transaction。 |
| `change_analyzer/` | 比较旧/新 CodeGraph，并将函数变更投影为具有双图路径证据的 `EntryChange`。 |
| `document_updater/` | 以纯函数方式读取和应用受管 Markdown fragment 的 desired state。 |
| `document_workbench/` | 按 document_key 保存成功结果；调用方每次重新分析，仅跳过已成功的昂贵生成。 |

依赖保持单向：

```text
file_tracker       code_graph
      \             /
       change_analyzer
              |
      caller workflow
          /       \
 document_updater document_workbench
```

`document_updater` 保持纯 Markdown fragment 合并。`document_workbench` 只管理成功结果检查点，
不保存处理输入，不执行源码扫描、分析或生成；assemble 协调原文读取与片段合并，
显式 publish 负责结果写入和清理。
这些业务规则由调用方及其他领域包负责。它依赖 `document_updater.DocumentResult`
存储片段结果，但不依赖 FileTracker。

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

多个片段使用 `apply_results(existing_markdown, tuple(results))`，只解析和渲染一次。
已有片段替换保留位置，新片段按结果顺序追加；重复 ID 按顺序生效，删除后重新插入会移到末尾。
空 tuple 保持原文不变，但仍校验受管标记是否合法。

受管内容采用 `codegraph:fragment` markers。UPSERT 在缺失时插入、存在时替换；DELETE 幂等删除。
`document_updater` 返回 Markdown 字符串，不执行物理文件删除。调用方拥有 fragment 来源、
`fragment_id -> document key` 映射、分组和审核队列。`document_workbench` 保存完整文档结果，
并通过显式 publish 写入目标目录和清理检查点；FileTracker baseline 推进是调用方独立执行的步骤。

支持一 Entry 一 fragment、多个 Entry 合成一个 fragment，或多个
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
uv run python examples/document_workbench_mvp.py
```

脚本各自在临时目录中演示调用方场景；真实恢复调用应使用稳定的源码与文档目录：

1. [`initial_documentation.py`](examples/initial_documentation.py)：调用共享 VBA 工作流执行首次生成并确认 baseline。
2. [`changed_module_documentation.py`](examples/changed_module_documentation.py)：使用相同工作流先生成，再更新共享 Module 并发布受影响 Entry。

示例约定 VBA 导出文件同级存放，并使用 `Form_*.txt` 和 `Module_*.txt` 命名。每个 Form 的完整
分析上下文是该 Form 加上全部 Module；`.txt` 文件由示例适配器显式标记为 VBA。Form 按
UTF-16 读取，并只将 `CodeBehindForm` 行之后的 VBA 代码交给 CodeGraph；Module 支持 UTF-8
或带 BOM 的 UTF-16。

[`document_workbench`](docs/document_recovery.md) 用一个 JSON 保存 document_key 成功索引，
完整结果单独保存在工作台。分析成本低，每次重新分析；只跳过已成功的昂贵文档生成：

```python
from document_workbench import Workbench

workbench_root = document_root / ".workbench"
workbench = Workbench.open(workbench_root, protected_roots=(source_root,))
changes = tracker.scan()
for document_key, analysis in analyze_documents(changes).items():
    if workbench.contains(document_key):
        continue
    content = render_document(analysis)
    workbench.save(document_key, content)

# workbench 可跨函数传递；全部结果准备好后再显式发布。
workbench.publish(document_root)

# baseline 提交是额外步骤，不由 publish 执行。
```

工作台不接收任务输入、Batch 或 prepare 回调。结果统一为 UTF-8 文本，编码由工作台负责。
results 返回已保存结果，不判断任务是否全量完成；调用方必须完成全部生成后再发布。
结果保存完成不代表发布成功。显式 publish 内部复制发布，成功后关闭并清理工作台；
发布失败保留检查点以便重试。无需 with 或 finally 来释放资源；close 仅使当前对象失效，
不删除结果。with 仍作为兼容接口保留，退出不自动发布。tracker.commit 是调用方的额外步骤。
同一批执行及恢复期间，调用方保证源码、原始文档和生成配置不被外部修改，且仅允许单写者；
输入变化时调用方保留必要数据并另开批次。工作台不固定原文快照，不检测外部编辑冲突。
库不加锁、不检测目录占用。调用方保证同一个 document_root 同时只有一个实例操作；
不同实例使用独立且互不嵌套的文档目录，不共享工作台。MVP 将工作台固定放在
document_root / ".workbench"，调用方扫描文档时必须排除它，文档 key 不得指向工作台。
MVP 读取未完成文档原文；generate_fragments 只生成片段结果，prepare_batch 使用 apply_results
合并目标片段，再保存完整文本并返回 Workbench，调用方可稍后 publish；
run_batch 是准备后立即发布的便利入口。生成与文本更新分开，无需复制文档目录。
生成中断不修改正式文档；部分发布失败则复用已保存结果重试发布，不重新生成。
其他示例的 DocumentPublisher 直接发布流程不受影响。
protected_roots 声明不可与工作台重叠的目录，路径校验由 open 内部执行。
存储 schema 为 7，可恢复 schema 5，后续状态提交写为 7；旧 schema 1/2/3/4/6 明确拒绝。
schema 6 的不透明文本任务需用此前版本完成后再升级。
此前 schema 5 留下的普通 .lock 文件仍可读取，发布后清理，但不再用于互斥。

同一批工作分 A/B 阶段且共享文档时，Workbench 按 `(document_key, task_id)`
保存 `tuple[DocumentResult, ...]` 检查点，不将 A 的部分结果当作完整文档：

```python
workbench = Workbench.open(document_root / ".workbench", protected_roots=(source_root,))
for task in tasks_a + tasks_b:
    if not workbench.contains_task(task.document_key, task.id):
        workbench.save_task(task.document_key, task.id, generate(task))
workbench.publish(document_root)  # 调用方确认全部阶段完成，自动组装后发布
```

任务项全部片段以稳定任务 ID 持久化，A 中途失败后可跳过已提交项；一个任务项对应一份文档。
合并按任务 ID 字典序、任务内部片段顺序执行，重复 fragment ID 沿用顺序应用规则。
首次 save 完整文档前自动持久化冻结任务集合，无需 seal_tasks；冻结后禁止添加任务。
task_results(document_key) 返回按任务 ID 排序的只读 mapping，值为不可变的结果 tuple；
task_documents() 枚举有任务记录的文档。任务结果与冻结标记位于 `.workbench/state.json`，
完整文档单独保存；publish 自动组装尚未保存的任务文档，
全部组装成功后才发布完整文档，成功后统一清理。
`document_workbench.TaskResult` 封装 document_key、task_id 和结果 tuple，
通过 `to_dict()` / `from_dict()` 负责检查点序列化与校验，不修改 DocumentResult。
Workbench 负责原子落盘；assemble 读取尚未保存文档的原文（不存在时为空），
调用 apply_results 并复用 save 保存完整文本。它不修改正式文档，重试跳过已保存文档，
原文读取或合并错误明确传播；调用方仍负责确认阶段完整性与保证输入不变。
也可独立调用 assemble，在发布前检查组装结果；仅通过 save 保存完整文本的流程保持不变。

## Setup and validation

Python 3.10+ 与 uv 是必需条件。

```bash
uv sync --all-packages
PYTHONPATH=. uv run pytest code_graph/tests file_tracker/tests change_analyzer/tests document_workbench/tests tests
uv build --all-packages
```

领域边界与完整生命周期见：

- [`docs/document-domain-model.md`](docs/document-domain-model.md)
- [`docs/document-lifecycle.md`](docs/document-lifecycle.md)
- [`docs/mvp-walkthrough.md`](docs/mvp-walkthrough.md)
