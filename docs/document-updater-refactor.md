# 文档更新重构设计

## 1. 目标

系统的目标不是“根据 Entry 自动同步某种固定形态的文档”，而是提供一条可组合的数据流：

```text
代码变更
  -> 受影响 Entry 的代码事实
  -> 任意内容生成请求
  -> 一个或多个文档片段结果
  -> 调用方决定片段归属、合并到哪份文档
  -> 对单份 Markdown 应用片段结果
  -> 调用方发布文档
```

其中：

- 代码变更如何影响 Entry，是静态分析问题；
- 用哪些代码或外部信息生成什么文档，是内容生成问题；
- 哪些片段属于同一文档，是文档编排问题；
- 如何将一个片段插入、替换或删除，是 Markdown 片段编辑问题；
- 路径、文件读写、重试、审核队列、baseline 推进是应用适配问题。

这些问题不可由一个隐藏应用策略的 façade 隐式解决。通过少量不可变值对象和纯函数显式组合它们。

## 2. 架构与依赖方向

```text
file_tracker + code_graph
            │
            ▼
     change_analyzer
     EntryChange[]                 代码影响事实
            │
            ▼
 documentation_workflow            调用方/应用层
  ├─ EntryChange -> AuthorRequest
  ├─ DocumentAuthor
  ├─ DocumentResult 分组到 DocumentKey
  └─ storage adapter + baseline policy
            │
            └──────────────────────► document_updater
                                      prompt/LLM + 单文档 fragment 编辑
```

允许依赖：

```text
file_tracker       code_graph
      \             /
       change_analyzer
              |
 documentation_workflow
       |
document_updater
```

`document_updater` 同时提供 authoring 与 fragment editing；它不依赖
`change_analyzer`、`code_graph`、`file_tracker` 或任何 I/O adapter。

> `documentation_workflow` 是调用方应用层的名称，不要求立即创建为 workspace package。示例、CLI、服务或未来的 orchestration package 均可承担该职责。只有存在两个以上独立调用方时，才将其提取为独立包。

## 3. 最小领域模型

### 3.1 代码影响领域

`change_analyzer` 已拥有 baseline/working 快照、CodeGraph、函数变更和 Entry 反向可达规则。因此它应对外提供 Entry 级变更事实，而非让 `document_updater` 再次查询 CodeGraph。

```python
@dataclass(frozen=True)
class EntryChange:
    entry_id: str
    old_entry: EntryPoint | None
    new_entry: EntryPoint | None
    old_context: AnalysisContext | None
    new_context: AnalysisContext | None
    evidence: tuple[EntryImpactEvidence, ...]
    function_changes: tuple[FunctionChange, ...]
    call_edge_changes: tuple[CallEdgeChange, ...]
    diagnostics: tuple[Diagnostic, ...]
```

它是 `ImpactReport` 的确定性投影：

```python
def entry_changes(report: ImpactReport) -> tuple[EntryChange, ...]:
    """Return one complete change fact per affected entry, ordered by entry_id."""
```

这不是文档计划，也没有 `create`、`update`、`archive`、`review` 等文档动作。

`EntryChange` 的不变量：

1. 每个受影响 Entry 恰有一个对象，按 `entry_id` 排序。
2. affected Entry 是 baseline 与 working 图上反向可达 Entry 的并集；Entry 本身变化也属于影响。
3. `old_context` 和 `new_context` 分别只来自可用的旧、新图；删除的 Entry 允许只有旧 context，新增的 Entry 允许只有新 context。
4. `evidence` 同时保留 old/new path，不能因一侧不存在而丢失另一侧事实。
5. `function_changes`、`call_edge_changes` 与 `diagnostics` 只含该 Entry 的 old/new bounded context 可见的事实，并保持确定性排序。
6. 无法到达任何 Entry 的函数变化仍属于 `ImpactReport.unassigned_changes`，不伪造 EntryChange。

### 3.2 文档作者领域

文档作者不需要知道 Entry、函数、路径或 XML。它只将调用方定义的结构化上下文渲染进模板，并为一个稳定片段生成内容。

```python
@dataclass(frozen=True)
class PromptBlock:
    name: str
    content: str

@dataclass(frozen=True)
class AuthorRequest:
    fragment_id: str
    instructions: str
    blocks: tuple[PromptBlock, ...]

class ResultKind(Enum):
    UPSERT = "upsert"
    DELETE = "delete"

@dataclass(frozen=True)
class DocumentResult:
    fragment_id: str
    kind: ResultKind
    markdown: str | None

class DocumentAuthor(Protocol):
    def author(self, request: AuthorRequest) -> DocumentResult:
        ...
```

`PromptBlock` 是唯一的 prompt context primitive。调用方可以用任意序列化格式填充 `content`（Markdown、XML、JSON 或纯文本）；作者只保证按请求中的顺序渲染。

`AuthorRequest` 的不变量：

- `fragment_id` 非空且稳定，由调用方定义；
- `instructions` 是本次 authoring 的完整行为要求；
- block 名称非空且在一次请求中唯一；
- blocks 保持调用方顺序，不由 Author 重新排序；
- 输入中不携带目标 Markdown 文件、物理路径或发布策略。

`DocumentResult` 是目标 fragment 的期望状态：

| Kind | `markdown` | 含义 |
| --- | --- | --- |
| `UPSERT` | 必须存在 | 该 ID 的 fragment 最终应为这段完整 Markdown；不存在则插入，存在则替换。 |
| `DELETE` | 必须为 `None` | 该 ID 的 fragment 最终不应存在。 |

`DocumentAuthor.author()` 只会产生 `UPSERT`。`DELETE` 由 workflow 在内容不再应存在时直接构造，不调用 LLM。

作者的具体实现需要两个更小的可替换接口：

```python
class PromptRenderer(Protocol):
    def render(self, request: AuthorRequest) -> str:
        ...

class LlmClient(Protocol):
    def generate(self, prompt: str) -> str:
        ...
```

`LlmDocumentAuthor` 组合上述接口：

```python
@dataclass(frozen=True)
class LlmDocumentAuthor:
    renderer: PromptRenderer
    client: LlmClient

    def author(self, request: AuthorRequest) -> DocumentResult:
        ...
```

它渲染、调用、清理 Markdown fence，并拒绝会破坏 fragment marker 的 LLM 输出。网络、认证、provider SDK 和重试由 `LlmClient` adapter 所有；作者不吞掉 provider 错误。

### 3.3 文档 fragment 编辑领域

`document_updater` 只认识一份 Markdown 与一个结果。它不认识 Entry、代码、LLM、source ID、document path、文件系统或多文档计划。

```python
@dataclass(frozen=True)
class AppliedDocument:
    markdown: str
    fragment_ids: tuple[str, ...]
    changed: bool

def apply_result(
    existing_markdown: str,
    result: DocumentResult,
) -> AppliedDocument:
    """Apply one desired fragment state to one Markdown document."""
```

authoring 与 fragment editing 共享 `document_updater` 内的 `ResultKind` 和 `DocumentResult`，不引入额外 package：

```text
document_updater.model
  └─ ResultKind, DocumentResult

document_updater.fragments
  └─ AppliedDocument, apply_result()

document_updater.author
  └─ imports DocumentResult, ResultKind only
```

`apply_result()` 的不变量：

1. `UPSERT` 对缺失和已有 fragment 均合法，分别表示新增和替换；文档层没有 Create/Update 区分。
2. `DELETE` 对缺失 fragment 是幂等 no-op，方便重复投递和恢复；它不删除整份物理文件。
3. 所有 fragment 使用唯一且规范的 `codegraph:fragment` start/end marker；fragment body 不得包含保留 marker。
4. 更新保持 fragment 原有位置；新 fragment 追加到现有受管 fragment 区域末尾。
5. 人工内容只允许存在于受管 fragment 区域前或后，并按原样保留；受管 fragments 间出现非空非受管内容时抛出 `InvalidManagedDocumentError`，因为其归属不明确。
6. 空字符串可以是调用方拥有的纯人工文档；`AppliedDocument.fragment_ids == ()` 只表示没有受管 fragment，由调用方决定删除物理文件还是保留 header。
7. 输出与 `fragment_ids` 顺序稳定；同样输入得到字节级一致输出。

推荐的受管格式：

```markdown
# Order workflow

Human-owned introduction.

<!-- codegraph:fragment:start fragment_id="entry:vba:frmOrder:bSave_Click" -->
## Save order

Validates and persists the current order.
<!-- codegraph:fragment:end fragment_id="entry:vba:frmOrder:bSave_Click" -->
```

不需要 source marker、section order、Entry marker 或自动生成标题。`markdown` 是完整 fragment body，标题是生成器/调用方内容的一部分。

## 4. 模块划分和公开接口

### `change_analyzer`

**能力**：从 FileTracker 的 immutable change set 和完整 working source snapshot 中构造 old/new CodeGraph，找出所有受影响 Entry 并保留可追溯证据。

**公开接口**：

```python
def analyze_changes(
    change_set: ChangeSet,
    working_sources: Sequence[SourceFile],
    analyzers: Sequence[LanguageAnalyzer],
) -> ImpactReport: ...

def entry_changes(report: ImpactReport) -> tuple[EntryChange, ...]: ...
```

**禁止依赖**：`change_analyzer`、`codegraph`、`filetracker`、LLM provider、Markdown、文件写入。

### `document_updater`

**能力**：将单个 fragment 的 desired state 应用到调用方已经选定的一份 Markdown 中。

**公开接口**：

```python
class ResultKind(Enum): ...

@dataclass(frozen=True)
class DocumentResult: ...

@dataclass(frozen=True)
class AppliedDocument: ...

def apply_result(
    existing_markdown: str,
    result: DocumentResult,
) -> AppliedDocument: ...

class InvalidDocumentResultError(ValueError): ...
class InvalidManagedDocumentError(ValueError): ...
```

**禁止依赖**：`change_analyzer`、`codegraph`、`filetracker`、LLM、`Path`、路径映射、文件读取/写入、批量分组、Entry。

### `document_updater` authoring API

**能力**：将调用方提供的 prompt blocks 转为 LLM request，并将一份 LLM 输出转为一个 `DocumentResult.UPSERT`。

**公开接口**：

```python
@dataclass(frozen=True)
class PromptBlock: ...

@dataclass(frozen=True)
class AuthorRequest: ...

class PromptRenderer(Protocol): ...
class LlmClient(Protocol): ...
class DocumentAuthor(Protocol): ...

@dataclass(frozen=True)
class LlmDocumentAuthor: ...
```

**禁止依赖**：`change_analyzer`、`codegraph`、`filetracker`、文件系统、document path、文档合并/发布策略。

provider 适配、prompt 策略和文档发布仍属于调用方；`document_updater` 只提供通用的
authoring 协议和确定性 fragment 编辑。

### `documentation_workflow`（调用方应用层）

**能力**：表达产品策略和 I/O：

- 选择哪些 `EntryChange` 需要生成、删除或进入 review；
- `EntryChange -> AuthorRequest` 的序列化与 prompt 策略；
- `fragment_id -> DocumentKey` 的映射；
- 将多个结果按 `DocumentKey` 分组；
- 读取/写入 Markdown，并逐条 fold `apply_result()`；
- 所有文档发布成功后，使用 `ImpactReport` revision 推进 FileTracker baseline。

**典型接口**（不属于核心 package）：

```python
class DocumentStore(Protocol):
    def load(self, key: DocumentKey) -> str: ...
    def save(self, key: DocumentKey, markdown: str) -> None: ...
    def delete(self, key: DocumentKey) -> None: ...

def request_for_entry(change: EntryChange) -> AuthorRequest: ...
def document_key_for(result: DocumentResult) -> DocumentKey: ...
def synchronize(report: ImpactReport) -> None: ...
```

`DocumentKey` 可以是 `str`，也可以是调用方自己的 frozen dataclass。它不应进入 `document_updater`。

## 5. 关键组合方式

### 一 Entry 对应一 fragment

这是默认策略，适合独立入口文档：

```python
for change in entry_changes(report):
    fragment_id = f"entry:{change.entry_id}"
    if change.new_entry is None:
        results.append(DocumentResult(fragment_id, ResultKind.DELETE, None))
    elif should_review(change):
        review_queue.enqueue(change)
    else:
        results.append(author.author(request_for_entry(change, fragment_id)))
```

新增和修改 Entry 都产生 `UPSERT`；删除 Entry 产生 `DELETE`。没有 `DocumentAction` 和 `EntryDocumentResult` 中间层。

### 多个 Entry 合成一个 fragment

这是调用方的 authoring/aggregation policy：

```python
order_entries = tuple(
    change for change in entry_changes(report)
    if belongs_to_order_workflow(change)
)
request = request_for_workflow(
    fragment_id="workflow:order",
    changes=order_entries,
)
result = author.author(request)
```

文档核心只看到 `workflow:order`，不会知道它由几个 Entry 得到。

### 多个 fragment 合并到一份文档

调用方先分组，不向核心传递 document path：

```python
for key, results in group_by_document_key(results):
    markdown = store.load(key)
    for result in results:
        markdown = apply_result(markdown, result).markdown
    store.save(key, markdown)
```

结果顺序是调用方的 layout policy。若有稳定顺序要求，调用方在分组后按自身规则排序，例如 `(document_key, fragment_id)`。

## 6. 删除的现有抽象

以下概念不应保留为 `document_updater` 的公开 API：

| 现有概念 | 去向 / 替代 |
| --- | --- |
| `EntryUpdatePlan`、`DocumentAction` | 删除；`EntryChange` 表达代码事实，`DocumentResult` 表达 fragment desired state。 |
| `EntryImpactContext`、`build_llm_context()` | 删除；`change_analyzer.entry_changes()` 返回完整 Entry 事实，应用层构造 `AuthorRequest`。 |
| 各类 `Prompt*Reference`、`LlmPromptPayload` | 删除；统一为 `PromptBlock`，序列化属于应用 prompt policy。 |
| `EntryDocumentResult` | 删除；统一为 `DocumentResult`。 |
| `DocumentSection`、`SectionMutation`、`SectionMutationAction` | 删除；统一为单个 `DocumentResult` 的 UPSERT/DELETE desired state。 |
| `SourceDocumentTarget`、`resolve_source_document_target()` | 删除；文档归属与路径映射属于 workflow。 |
| `DocumentMutation`、`DocumentSyncPlan` | 删除；物理发布是 `DocumentStore` adapter 的职责。 |
| `LocalDocumentStore` | 移至应用 adapter/example；不属于 fragment core。 |
| `PendingReview` | 移至 workflow；审核不是文档 mutation。 |
| legacy `codegraph:entry` marker | 删除兼容；新实现仅解析 canonical fragment marker。 |

## 7. 重构落地顺序

1. 在 `change_analyzer` 增加 `EntryChange` 与 `entry_changes(report)`，以现有双图反向可达实现为唯一事实来源，并补充投影、删除、新增、共享依赖与 unassigned 的测试。
2. 将 `DocumentResult`、`ResultKind` 和单文档 `apply_result()` 定义为 `document_updater` 仅有的领域核心；替换 section/batch/path 模型并为解析、UPSERT、DELETE、保留人工内容和幂等性建立测试。
3. 将当前 authoring 示例改为通用 `document_updater` API：以 `AuthorRequest`、`PromptBlock` 和 `DocumentResult` 为输入输出，移除 `LlmEntryContext`。
4. 将 `examples/documentation_lifecycle.py` 改为 workflow 示例：将 `EntryChange` 显式转换成 request/result、按调用方规则映射并分组、逐个应用结果、最后发布与推进 baseline。
5. 删除 `document_updater` 内所有 Entry、CodeGraph、LLM、文件系统和 target mapping 实现，清理 runtime workspace dependencies；同步改写 README 和领域文档。

## 8. 完成标准

- `change_analyzer` 能只通过 `ImpactReport` 导出完整、确定性的 Entry 影响事实。
- `document_updater` 可由任意内容上下文驱动，且每次返回一个有效 `DocumentResult`。
- `document_updater` 对单个结果进行纯、确定性、幂等的 fragment 更新，且没有其他 workspace package 依赖。
- 应用层可以选择一 Entry 一 fragment、多个 Entry 一 fragment，或多个 fragment 一文档，而无需修改上述核心模块。
- 文件路径、存储、LLM provider、审核和 baseline 均不会泄漏进核心模型或形成反向依赖。
