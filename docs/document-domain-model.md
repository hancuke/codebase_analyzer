# 文档更新领域模型

文档生命周期只由三类事实组成：

```text
EntryChange
  -> caller creates DocumentResult
  -> apply_result(existing Markdown, DocumentResult)
  -> caller publishes the Markdown and advances its baseline
```

`change_analyzer` 拥有代码变更与 Entry 影响事实；调用方拥有内容生成、文档归属和 I/O；
`document_updater` 只拥有受管 Markdown fragment 的 desired state 与纯编辑规则。

## `document_updater` primitives

```python
class ResultKind(Enum):
    UPSERT = "upsert"
    DELETE = "delete"

@dataclass(frozen=True)
class DocumentResult:
    fragment_id: str
    kind: ResultKind
    markdown: str | None = None

def get_fragment_markdown(
    existing_markdown: str,
    fragment_id: str,
) -> str | None: ...

def apply_result(
    existing_markdown: str,
    result: DocumentResult,
) -> str: ...
```

`DocumentResult` 是一个 stable fragment 的完整期望状态：

| kind | `markdown` | 含义 |
| --- | --- | --- |
| `UPSERT` | 非空 | 插入缺失 fragment 或完整替换已有 fragment。 |
| `DELETE` | `None` | 删除 fragment；缺失时为幂等 no-op。 |

fragment ID 必须非空、稳定且 marker-safe。fragment body 不得包含保留的
`codegraph:fragment` marker。`apply_result()` 返回更新后的 Markdown；调用方在需要时比较输入和
输出即可知道是否改变。它保留人工 Markdown，拒绝损坏、嵌套、重复或在受管 fragments 之间混入无
归属内容的 marker 格式。它没有路径、文件系统、Entry、LLM 或批量发布概念。

`get_fragment_markdown()` 复用相同的 parser，读取一个已有受管 fragment 的 body；它让调用方在
生成内容前区分首次创建和更新，而不需要自行解析 markers。

## 调用方流程

调用方把 `EntryChange`、UI 控件提取结果或任意其他内容事实转换为 `DocumentResult`。同一个
文档可接收多个独立 producer 的结果，但每次运行中同一文档的同一 `fragment_id` 只能出现一次；
重复必须在写入前失败。

文档归属是调用方规则：`fragment_id -> document key` 可以是一 Entry 一 fragment、一组 Entry
一 fragment，或多个 fragment 一文档。调用方按 document key 分组，按其 layout policy 依次
fold `apply_result()`，然后负责读取、写入、空文件删除和成功发布后的 FileTracker baseline
commit。空结果列表也是合法的参与文档，调用方可直接从分组 mapping 的 keys 得到本次涉及的文档。

## LLM 内容生成是调用方策略

提示词、模型 provider、重试和最终 Markdown 合并不属于 fragment 编辑领域。调用方为一个
Entry 返回稳定有序的 1..N 个 `(system_prompt, user_prompt)` 对，依次调用自己的 LLM client，
并将结果合并为一个 `DocumentResult(UPSERT)`。核心不会理解“业务流程”“边界分析”等视角，也不会
把多次调用暴露成多个会互相覆盖的 fragment。

示例用普通函数 `generate_entry_document()` 封装这些调用方规则，非 LLM 路径保留
`generate_ui_document()` 接口；两者只共享 `DocumentResult`，不引入生成器基类。
`DocumentPublisher` 仅按调用方指定的 document key 读取片段和落盘，普通 `synchronize()`
函数协调发布与 baseline commit，不需要带生成行为的 `EntryDocument` 或额外的运行结果对象。

首次创建的 prompt 只传 bounded `new_context`、必要元数据和写作约束。它不得传
`code_change`、完整 diff、旧代码或旧 fragment：当前代码已是首次文档的唯一行为真相。更新已有
fragment 时，prompt 才传当前 fragment、当前上下文、与该 Entry 有关的受限变更摘要，以及无法由
当前代码得知的删除事实。删除 Entry 时调用方直接构造 `DELETE`，不调用 LLM。

当调用方需要固定章节、冲突解决或额外 synthesis 时，在其自身 workflow 中实现；无需向
`document_updater` 添加 prompt、perspective、author 或 merger 抽象。
