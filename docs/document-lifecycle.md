# 文档生成、更新与存储生命周期

本文说明代码变更如何最终变成 Markdown 文档，以及系统如何确定：

- 哪些入口需要生成或更新文档；
- 一个入口的文档写入哪个物理文件；
- 同一源文件中的多个入口如何共享一个文档；
- `create`、`update`、`archive`、`review` 分别如何处理；
- 路径、入口归属和受管区块等元数据保存在哪里。

## 1. 核心原则

系统使用两个不同粒度：

| 阶段 | 粒度 | 原因 |
| --- | --- | --- |
| 代码影响分析和 LLM 调用 | 一个 entry point | 限制上下文范围，让每次分析只解释一个入口 |
| Markdown 存储和发布 | 一个 source file | 同一源文件中的入口保存在同一个物理文档中 |

因此：

```text
一个 EntryUpdatePlan
    -> 一次单入口 LLM 分析
    -> 一个 EntryDocumentResult

同一 source_id 的多个 EntryDocumentResult
    -> 合并成一个 Markdown 文档
```

例如：

```text
forms/frmOrder.frm
├── vba:frmOrder:bSave_Click
└── vba:frmOrder:bCancel_Click
```

两个入口分别调用 LLM，但最终共同写入：

```text
<project_root>/docs/forms/frmOrder.md
```

## 2. 完整生命周期

```text
FileTracker.scan()
    -> baseline / working 源码快照
    -> analyze_changes()
    -> ImpactReport
    -> create_entry_plans()
    -> EntryUpdatePlan[]
    -> resolve_document_targets()
    -> SourceDocumentTarget[]
    -> LocalDocumentStore.load()
    -> 提取当前 entry 的旧文档
    -> 每个 plan 独立调用 LLM
    -> EntryDocumentResult[]
    -> build_document_sync_plan()
    -> DocumentSyncPlan
       ├── DocumentMutation[]: write / delete
       └── PendingReview[]
    -> LocalDocumentStore.apply()
    -> 所有文档成功后提交 FileTracker baseline
```

### 2.1 检测代码变化

`FileTracker` 保存 baseline，并扫描当前工作目录。`analyze_changes()` 使用 baseline 和 working
两份完整源码快照建立旧、新 CodeGraph，然后计算：

- 新增、修改和删除的函数；
- 新增和删除的可靠调用边；
- 每个变化函数能够影响到的 entry point；
- 旧图和新图中的调用路径证据。

删除的函数仍可通过旧图找到原入口，新增加的调用关系则通过新图发现。

`FileTracker` 应排除文档输出和 review queue。这样文档发布不会改变刚刚
扫描到的 working revision，随后才能安全地使用该 revision 推进 baseline：

```python
tracker = FileTracker(
    str(project_root),
    exclude_patterns=[
        "docs",
        "**/docs/**",
        ".codegraph-reviews",
        "**/.codegraph-reviews/**",
    ],
)
```

### 2.2 为每个入口创建 plan

`create_entry_plans(report)` 为每个受影响入口创建一个 `EntryUpdatePlan`。plan 的身份是稳定的
`entry_id`，并包含该入口相关的函数变化、调用边变化、旧新依赖上下文和 diagnostics。

动作由入口在旧、新 CodeGraph 中是否存在决定：

| Action | 旧图 | 新图 | 含义 |
| --- | --- | --- | --- |
| `create` | 不存在 | 存在 | 新入口，需要创建入口文档区块 |
| `update` | 存在 | 存在 | 入口仍存在，但相关行为发生变化 |
| `archive` | 存在 | 不存在 | 入口已删除，需要移除入口文档区块 |
| `review` | 任意 | 任意 | 上下文包含 error diagnostic，禁止自动发布 |

### 2.3 解析文档目标

`resolve_document_targets(plans)` 不解析 `entry_id` 来猜文件位置，而是从 plan 对应的
`AnalysisContext` 中找到入口 `Function`，读取其规范化 `Function.source_id`。

不同动作使用的上下文如下：

| Action | `source_id` 来源 |
| --- | --- |
| `create` | working/new context 中的入口函数 |
| `update` | 优先 working/new context，旧上下文用于一致性确认 |
| `archive` | baseline/old context 中的入口函数 |
| `review` | 优先 working/new context，没有时使用 baseline/old context |

这样即使入口已经从 working 源码中删除，`archive` 仍能从 baseline 得到原来的文档位置。

### 2.4 读取现有文档并构建 LLM 输入

`LocalDocumentStore.load(targets)` 从 `project_root` 下读取目标文档。返回值以相对
`document_path` 为键：

```python
{
    "docs/forms/frmOrder.md": "...current Markdown..."
}
```

一个物理文档可能包含多个入口。构建某个 plan 的 prompt 时，不能把整个共享文档传给 LLM，
而应使用 `extract_entry_document()` 只提取当前 `entry_id` 的区块：

```python
old_entry_document = extract_entry_document(
    current_document,
    source_id=target.source_id,
    entry_id=plan.entry_id,
)

context = build_llm_context(
    plan,
    old_document=old_entry_document or "",
)
```

这保证 LLM 只更新当前入口，不会重新解释或改写同文件中的其他入口。

### 2.5 保存 LLM 结果

每次 LLM 调用的结果包装为：

```python
EntryDocumentResult(
    entry_id=plan.entry_id,
    markdown=llm_markdown,
)
```

`markdown` 是当前入口的正文，不包含：

- source 文档标题；
- entry 标题；
- `<!-- codegraph:... -->` 管理标记；
- 物理路径。

这些结构由同步层统一生成，LLM 不能控制。若结果包含保留的 `codegraph` 标记，同步构建会失败，
避免 LLM 伪造区块边界或覆盖其他入口。

### 2.6 合并成 source 级文档

`build_document_sync_plan()` 会：

1. 校验 plan 和 LLM result 是否一一匹配；
2. 将 plan 按 `source_id` 分组；
3. 严格解析现有 Markdown 中的受管区块；
4. 在内存中应用所有 `create`、`update`、`archive`；
5. 按 `entry_id` 稳定排序；
6. 为每个物理文档最多生成一个最终 write 或 delete mutation；
7. 将 `review` 单独放入 `pending_reviews`，不修改正式文档。

只有全部输入和文档结构都校验成功后，才会得到 `DocumentSyncPlan`。因此不会出现同一个
source file 的第一个 plan 已经写入，而第二个 plan 在合并阶段失败的情况。

### 2.7 发布并推进 baseline

`LocalDocumentStore.apply(sync_plan)`：

- 为 write mutation 创建父目录并原子替换目标文件；
- 为 delete mutation 删除目标文件；
- 将 pending review 写入审核目录。

单个文件使用“同目录临时文件 + `os.replace()`”原子写入。多个文件之间不是全局事务，因此只有所有
文档同步函数不应隐式提交 FileTracker baseline。所有正式文档 mutation 都发布成功、且没有
`pending_reviews` 后，由外部流程按自己的事务边界单独提交 baseline：

```python
file_tracker.commit(
    message="documentation synchronized",
    expected_revision=change_set.working_revision,
    expected_baseline_revision=change_set.baseline_revision,
)
```

发布失败时不推进 baseline，下次运行仍会分析同一批代码变化。
`review` 会保留待审核的代码变更，不应推进 baseline；否则下次运行将无法再生成对应的
审核任务。

## 3. 文档路径规则

### 3.1 `project_root` 是什么

`project_root` 是 `LocalDocumentStore` 的物理工作区根目录：

```python
store = LocalDocumentStore(project_root)
```

所有正式文档路径和 review 路径最终都相对于它解析。假设：

```text
project_root = /workspace/order-system
```

那么相对文档路径：

```text
docs/forms/frmOrder.md
```

对应物理路径：

```text
/workspace/order-system/docs/forms/frmOrder.md
```

路径解析后必须仍位于 `project_root` 内，否则抛出 `InvalidDocumentPathError`。

### 3.2 正式文档映射

默认 `docs_root` 是 `docs`。映射算法是：

```text
document_path =
    docs_root
    / source_id 保留目录结构并把最后一个文件后缀替换为 .md
```

示例：

| `source_id` | 默认 `document_path` | 物理位置 |
| --- | --- | --- |
| `frmOrder.frm` | `docs/frmOrder.md` | `<project_root>/docs/frmOrder.md` |
| `forms/frmOrder.frm` | `docs/forms/frmOrder.md` | `<project_root>/docs/forms/frmOrder.md` |
| `packages/order.pkb` | `docs/packages/order.md` | `<project_root>/docs/packages/order.md` |

调用方可以修改文档根目录：

```python
targets = resolve_document_targets(plans, docs_root="generated/docs")
sync_plan = build_document_sync_plan(
    plans,
    results,
    existing_documents,
    docs_root="generated/docs",
)
```

两次调用必须使用相同的 `docs_root`。

`source_id` 和 `docs_root` 都必须是规范化的相对 POSIX 路径：

- 不能以 `/` 开头；
- 不能包含 `.` 或 `..` 路径段；
- `source_id` 必须有文件后缀；
- 两个不同 `source_id` 不能映射到同一文档路径。

### 3.3 Review 文档路径

`review` 默认写入：

```text
<project_root>/.codegraph-reviews/<source path without suffix>/
    <sanitized-entry-id>-<entry-id-hash>.md
```

例如：

```text
source_id = forms/frmOrder.frm
entry_id  = vba:frmOrder:bSave_Click
```

得到的目录形态是：

```text
<project_root>/.codegraph-reviews/forms/frmOrder/
    vba_frmOrder_bSave_Click-<12位hash>.md
```

hash 用于避免不同 entry ID 清理为相同文件名。可以通过 `LocalDocumentStore` 的
`review_root` 参数修改审核目录：

```python
store = LocalDocumentStore(
    project_root,
    review_root="review-queue",
)
```

## 4. Markdown 中保存的元数据

系统当前不维护独立 catalog。入口到源文件、源文件到物理文档的关系由分析结果和确定性路径规则
得到；文档自身只保存同步所需的最小元数据。

一个共享文档的结构如下：

```markdown
<!-- codegraph:source source_id="forms/frmOrder.frm" -->
# frmOrder.frm

<!-- codegraph:entry:start entry_id="vba:frmOrder:bCancel_Click" -->
## bCancel_Click

取消当前订单编辑。
<!-- codegraph:entry:end entry_id="vba:frmOrder:bCancel_Click" -->

<!-- codegraph:entry:start entry_id="vba:frmOrder:bSave_Click" -->
## bSave_Click

校验权限并保存当前订单。
<!-- codegraph:entry:end entry_id="vba:frmOrder:bSave_Click" -->
```

### 4.1 Source 元数据

```markdown
<!-- codegraph:source source_id="forms/frmOrder.frm" -->
```

它声明整个 Markdown 属于哪个规范化源文件。读取文档时，如果 marker 中的 `source_id` 与目标
`source_id` 不一致，同步会失败，避免把一个源文件的结果写进另一个源文件的文档。

### 4.2 Entry 元数据

```markdown
<!-- codegraph:entry:start entry_id="vba:frmOrder:bSave_Click" -->
...
<!-- codegraph:entry:end entry_id="vba:frmOrder:bSave_Click" -->
```

完整 `entry_id` 是区块的稳定身份。可见的 `## bSave_Click` 只用于阅读，不参与定位。更新和删除
始终依靠 marker，不依赖标题文本，因而标题或正文内容的变化不会破坏区块识别。

解析器拒绝：

- 嵌套 entry marker；
- 重复 entry ID；
- start/end ID 不一致；
- 缺少 start 或 end；
- 未识别的 `<!-- codegraph:... -->` 标记；
- 两个受管 entry 区块之间存在归属不明确的非空内容。

文档首尾未被 marker 管理的人工内容会保留。

### 4.3 运行时元数据模型

| 类型 | 关键字段 | 用途 |
| --- | --- | --- |
| `EntryUpdatePlan` | `entry_id`, `action`, old/new context, changes | 描述一个入口为什么需要处理 |
| `SourceDocumentTarget` | `source_id`, `document_path` | 描述源文件对应的正式文档 |
| `EntryDocumentResult` | `entry_id`, `markdown` | 保存一次单入口 LLM 结果 |
| `DocumentMutation` | `action`, `target`, `content` | 描述一个物理文件的最终写入或删除 |
| `PendingReview` | `entry_id`, `source_id`, `document_path`, `markdown` | 保存禁止自动发布的分析结果 |
| `DocumentSyncPlan` | `mutations`, `pending_reviews` | 一次发布前完整、已校验的同步计划 |

这些对象都是不可变 dataclass。`DocumentSyncPlan` 是发布层的输入，而不是再次让发布层推导业务
动作。

## 5. 四种动作的写入语义

| Action | 是否需要 LLM result | 正式文档行为 | 状态冲突 |
| --- | --- | --- | --- |
| `create` | 必须 | 插入新 entry 区块 | 区块已经存在则失败 |
| `update` | 必须 | 只替换对应 entry 区块 | 区块不存在则失败 |
| `archive` | 不接受 | 删除对应 entry 区块 | 区块不存在则失败 |
| `review` | 可选 | 不修改正式文档 | 保存到 review queue |

### 5.1 Create

如果目标文档不存在，同步层先生成 source marker 和一级标题，再插入入口区块。如果同一轮有多个
新入口属于同一个 `source_id`，它们会一次性合并为一个 write mutation。

### 5.2 Update

更新只替换匹配 `entry_id` 的 marker 区间。其他入口区块、文档首尾的人工内容都会保留。

`update` 要求旧区块必须存在。这是有意的严格策略：如果代码分析认为入口是 update，但文档中没有
对应区块，说明文档状态与 baseline 不一致，系统不会静默地把 update 降级为 create。

### 5.3 Archive

删除入口时不调用 LLM。同步层移除对应 marker 区间：

- 还有其他 entry：重写文档并保留其他 entry；
- 没有其他 entry，但有人工内容：保留文件和人工内容；
- 没有其他 entry，且只剩自动生成的 source header：删除物理文件。

### 5.4 Review

当 plan 为 `review` 时，即使存在 LLM 结果，也不会产生正式文档 mutation。结果和目标元数据写入
review queue，等待人工确认后再由外部流程决定是否发布。

## 6. 同一源文件多入口示例

初始源码：

```text
forms/frmOrder.frm
├── bSave_Click
└── bCancel_Click
```

首次生成得到两个 plan：

```text
create vba:frmOrder:bSave_Click
create vba:frmOrder:bCancel_Click
```

分别调用 LLM 后，按共同的 `source_id=forms/frmOrder.frm` 聚合，最终只有一个 mutation：

```text
write docs/forms/frmOrder.md
```

之后只修改 `bSave_Click` 依赖的业务函数，得到：

```text
update vba:frmOrder:bSave_Click
```

同步层读取 `docs/forms/frmOrder.md`，只提取并替换 `bSave_Click` 区块，
`bCancel_Click` 区块保持不变。

如果随后删除 `bCancel_Click`：

```text
archive vba:frmOrder:bCancel_Click
```

同步层只删除它的区块。因为 `bSave_Click` 仍然存在，`docs/forms/frmOrder.md` 不会被删除。

## 7. 调用顺序示例

```python
from document_updater import (
    DocumentAction,
    EntryDocumentResult,
    LocalDocumentStore,
    build_document_sync_plan,
    build_llm_context,
    extract_entry_document,
    resolve_document_targets,
)

store = LocalDocumentStore(project_root)
targets = resolve_document_targets(plans)
targets_by_source = {target.source_id: target for target in targets}
existing_documents = store.load(targets)

results = []
for plan in plans:
    if plan.action is DocumentAction.ARCHIVE:
        continue

    source_id = next(
        target.source_id
        for target in targets
        if plan.entry_id in {
            function.id
            for context in (plan.entry.old_context, plan.entry.new_context)
            if context is not None
            for function in context.functions
        }
    )
    target = targets_by_source[source_id]
    current_document = existing_documents.get(target.document_path, "")
    old_entry_document = (
        extract_entry_document(
            current_document,
            source_id=source_id,
            entry_id=plan.entry_id,
        )
        if current_document
        else None
    )
    llm_context = build_llm_context(
        plan,
        old_document=old_entry_document or "",
    )
    llm_markdown = call_llm(llm_context.to_reference_data_xml())
    results.append(EntryDocumentResult(plan.entry_id, llm_markdown))

sync_plan = build_document_sync_plan(
    plans,
    results,
    existing_documents,
)
store.apply(sync_plan)
```

实际调用方可以在生成 prompt 前建立更直接的 `entry_id -> SourceDocumentTarget` 索引；关键约束是
读取、构建 sync plan 时使用同一套 `source_id` 和 `docs_root`。

## 8. 失败与恢复

以下情况会在发布前失败：

- 缺少 `create` 或 `update` 的 LLM result；
- result 对应未知或重复的 entry；
- `archive` 错误地携带 LLM result；
- 路径不安全或两个 source 映射冲突；
- Markdown marker 损坏；
- create/update/archive 与当前文档状态冲突；
- LLM 正文包含保留 marker。

处理原则是：

```text
先完整校验和构建 DocumentSyncPlan
    -> 再发布文件
    -> 全部成功后推进 baseline
```

如果构建或发布失败，不提交 baseline。修复问题后重新运行即可重新处理相同代码变化。
