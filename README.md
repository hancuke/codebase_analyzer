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

## Setup and validation

Python 3.10+ 与 uv 是必需条件。

```bash
uv sync --all-packages
uv run pytest code_graph/tests file_tracker/tests change_analyzer/tests document_updater/tests
uv build --all-packages
```

领域边界与完整生命周期见：

- [`docs/document-domain-model.md`](docs/document-domain-model.md)
- [`docs/document-lifecycle.md`](docs/document-lifecycle.md)
- [`docs/mvp-walkthrough.md`](docs/mvp-walkthrough.md)
