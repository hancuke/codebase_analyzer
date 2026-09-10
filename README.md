# Code documentation workspace

这是一个使用 [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/)
管理的 Python 项目，用于：

1. 跟踪代码文件相对于 baseline 的变化；
2. 分析完整代码快照中的函数、调用关系和入口；
3. 判断一次代码变更影响了哪些入口；
4. 根据入口—文档覆盖关系，为每个受影响文档生成独立的更新计划；
5. 构建可以交给 LLM 的单文档更新上下文。

当前示例主要使用 Microsoft Access VBA：

```text
FileTracker ChangeSet
    -> baseline / working SourceFile snapshots
    -> old / new CodeGraph
    -> function and call-edge changes
    -> affected entries and path evidence
    -> document update plans
    -> LLM-ready context
```

## Workspace packages

| Package | Responsibility |
| --- | --- |
| `code_graph/` | 从完整源码快照提取函数、调用关系、入口和 diagnostics，并提供依赖图查询 |
| `file_tracker/` | 扫描物理文件变化，保存 baseline，提供不可变的文件内容快照和 revision 校验 |
| `change_analyzer/` | 比较旧、新 CodeGraph，将函数和调用边变化映射到受影响入口 |
| `document_updater/` | 维护入口到文档的覆盖关系，将入口影响转换成文档动作，并构建 LLM 文档更新上下文 |

依赖方向保持单向：

```text
filetracker       codegraph
      \             /
       change-analyzer
              |
       document-updater
```

`codegraph` 不读取文件系统，`filetracker` 不理解调用图，文档和 LLM 逻辑也不会进入这两个
核心包。

## Requirements

- Python 3.10 或更高版本
- uv

安装 uv：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Setup

在 workspace 根目录运行：

```bash
uv sync --all-packages
```

根目录的 `uv.lock` 统一锁定所有 workspace package 的依赖，不需要在各子目录维护独立
lock file。

## Run the MVP example

```bash
uv run python examples/entry_document_update.py
```

示例会在临时目录中建立一个小型 VBA 项目：

```text
frmCustomer.bDelete_Click
    -> CheckPermission

frmOrder.bSave_Click
    -> CheckPermission
    -> SaveOrder

frmReport.bRefresh_Click
    -> RefreshReport
```

建立 baseline 后，示例会：

- 修改共享函数 `CheckPermission`；
- 修改订单函数 `SaveOrder`；
- 新增 `AuditOrder`；
- 修改独立的 `RefreshReport`；
- 扫描文件变化；
- 构建旧、新两张 CodeGraph；
- 计算受影响入口；
- 将相关变化划分成独立的 impact batches；
- 为每个受影响文档生成文档计划；
- 输出一份截断后的 LLM-ready context。

预期影响分组：

```text
Batch 1
    frmCustomer.bDelete_Click
    frmOrder.bSave_Click

    shared change:
        CheckPermission

    order-only changes:
        SaveOrder
        AuditOrder

Batch 2
    frmReport.bRefresh_Click

    change:
        RefreshReport
```

共享函数可以同时影响多个入口。这是多对多关系，不会被强制归属到单一入口。

## Run tests

运行整个 workspace：

```bash
uv run pytest
```

运行指定 package：

```bash
uv run pytest change_analyzer/tests
uv run pytest document_updater/tests
```

运行单个测试：

```bash
uv run pytest \
  document_updater/tests/test_planner.py::test_empty_baseline_creates_documents_for_new_entries
```

构建所有 package：

```bash
uv build --all-packages
```

项目当前没有配置 lint 工具。

## Minimal API flow

### 1. Track file changes

```python
from filetracker import FileTracker

file_tracker = FileTracker(
    root="./example_project",
    exclude_patterns=["**/__pycache__/**", "**/*.pyc"],
)

change_set = file_tracker.scan()
```

`scan()` 是只读操作，返回：

- `baseline_revision`
- `working_revision`
- added、modified、deleted 文件
- 每个变化文件的 baseline 和 working 内容快照

### 2. Supply the complete working source snapshot

`analyze_changes()` 需要当前项目的全部受支持源码，而不只是变化文件：

```python
from pathlib import Path

from codegraph import SourceFile


def load_sources(root: Path) -> tuple[SourceFile, ...]:
    return tuple(
        SourceFile(
            source_id=path.relative_to(root).as_posix(),
            content=path.read_text(encoding="utf-8"),
        )
        for path in sorted(root.rglob("*"))
        if path.suffix.casefold() in {".bas", ".cls", ".frm"}
    )
```

这是必要条件，因为一个修改函数的入口可能位于完全没有修改的文件中。

### 3. Analyze entry impacts

```python
from change_analyzer import analyze_changes, cluster_impacts
from codegraph import VbaAnalyzer

working_sources = load_sources(Path("./example_project"))

report = analyze_changes(
    change_set=change_set,
    working_sources=working_sources,
    analyzers=[VbaAnalyzer()],
)

for impact in report.entry_impacts:
    print(impact.entry_id)
    for evidence in impact.evidence:
        print(evidence.changed_function_id)
        print(evidence.old_paths)
        print(evidence.new_paths)

for batch in cluster_impacts(report):
    print(batch.entry_ids, batch.changed_function_ids)
```

`ImpactReport` 主要包含：

| Field | Meaning |
| --- | --- |
| `old_codebase` | baseline 完整代码图 |
| `new_codebase` | working 完整代码图 |
| `function_changes` | added、modified、deleted 函数 |
| `call_edge_changes` | resolved 调用边的新增和删除 |
| `entry_impacts` | 每个入口的变化证据和旧、新路径；随后按文档 coverage 聚合 |
| `unassigned_changes` | 没有任何可达入口的函数变化 |
| `snapshot_issues` | 无法加入某一侧源码快照的文件 |

受影响入口使用旧、新图结果的并集：

```text
baseline 中能够到达变化函数的入口
UNION
working 中能够到达变化函数的入口
```

因此删除的函数和调用关系不会因只存在于旧图而被漏掉，新增加的关系也可以从新图发现。

### 4. Register document coverage and create plans

文档不是强制“一入口一文件”。版本控制的 `DocumentCatalog` 声明一份文档覆盖哪些入口；同一个
Access Form 的多个事件入口可归入同一份文档。完整领域模型见
[`docs/document-domain-model.md`](docs/document-domain-model.md)。

```python
from document_updater import DocumentCatalog, create_document_plans

catalog = DocumentCatalog.load("docs/document-registry.json")
plans = create_document_plans(
    report,
    document_registry=catalog,
)

for plan in plans:
    print(plan.action.value, plan.entry_id, plan.document_path)
```

支持的文档动作：

| Action | Meaning |
| --- | --- |
| `create` | 新入口，需要创建文档 |
| `update` | 入口仍存在，但相关代码发生变化 |
| `archive` | 入口已删除，文档应归档或由调用方决定如何处理 |
| `review` | 分析包含 error diagnostic，不应直接自动发布 |

清单的最小格式如下：

```json
{
  "version": 1,
  "documents": [
    {
      "path": "docs/forms/order.md",
      "entry_ids": [
        "vba:frmOrder:bCancel_Click",
        "vba:frmOrder:bSave_Click"
      ]
    }
  ]
}
```

清单未登记的入口保持兼容，默认文档路径类似：

```text
docs/entries/vba/frmOrder/bSave_Click.md
```

### 5. Build an LLM context

```python
from document_updater import build_llm_context

context = build_llm_context(
    plans[0],
    old_document="# Existing entry document\n",
)

prompt = context.to_prompt()
```

生成的上下文包括：

- 文档动作和目标路径；
- 旧文档；
- 与该入口有关的函数 diff；
- 调用边变化；
- 旧、新调用路径证据；
- baseline entry dependency context；
- working entry dependency context；
- 相关 diagnostics。

LLM 每次只处理一个文档计划；计划会包含该文档内每个受影响入口的独立证据和上下文，不需要从整个
repository diff 中猜测入口归属。

### 6. Advance the baseline after publishing

当前 MVP 不负责调用 LLM 或发布文档。调用方应先生成、验证并发布全部目标文档，最后才
推进 FileTracker baseline：

```python
file_tracker.commit(
    message="documentation synchronized",
    expected_revision=change_set.working_revision,
    expected_baseline_revision=change_set.baseline_revision,
)
```

任一文档失败时不要提交 baseline，这样下次可以重新处理同一组代码变化。

## Empty baseline initialization

首次运行时 baseline 可以为空：

```text
empty baseline
    -> all current files are added
    -> old CodeGraph is empty
    -> new CodeGraph contains the complete project
    -> all functions are added
    -> each discovered entry receives a create plan
```

入口依赖的新增函数会归入相应入口计划。没有入口可以到达的函数会保留在
`report.unassigned_changes`，不会静默丢失。

## Current MVP boundaries

当前实现覆盖：

- 完整 baseline/working 快照重建；
- 函数 added、modified、deleted；
- resolved 调用边变化；
- 旧、新图反向入口影响；
- 调用路径证据；
- 多入口共享变化；
- 独立影响批次；
- 空 baseline 初始化；
- 单文档更新计划和 LLM 上下文。

尚未实现：

- LLM provider 集成；
- 文档 staging、校验和原子发布；
- 自动提交 FileTracker baseline；
- 函数重命名推断；
- Access Form 非过程属性变化；
- VBA 动态调用和宏关系；
- 跨语言调用关系；
- 共享能力文档生成。

更详细的 MVP 说明参见 [`docs/mvp-walkthrough.md`](docs/mvp-walkthrough.md)。
