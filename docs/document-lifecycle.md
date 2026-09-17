# 文档生命周期

```text
example caller workflow
  1. FileTracker.scan()                  # read-only source revision
  2. affected_entries_by_form(...)       # analyze each affected Form
  3. publish_documents_for_forms(...)    # generate, group, and persist results
  4. FileTracker.commit(...)             # only after publication succeeds
```

首次生成和后续更新使用同一条编排流程。第一次运行时 baseline manifest 不存在，`scan()` 将当前
源码视为 added files；`analyze_changes()` 得到没有 `old_context` 的新增 Entry。目标 fragment
不存在，因此生成 prompt 只包含当前 bounded code context，发布后提交当前源码为 baseline。

之后再次运行时，`scan()` 将当前源码与上一次 commit 的 baseline 比较。修改依赖函数时，
`analyze_changes()` 使用 baseline 和 working 两张图的反向可达并集找到受影响 Entry；已有 fragment
会被读入 prompt，同时加入当前代码和受限变更摘要。Entry 被删除时则生成同一 fragment ID 的
`DELETE` result。发布全部结果后，才提交新的 baseline。

因此 `tracker.commit()` 不是“开始处理”的步骤，而是本次文档发布成功后的确认点。分析或文档发布
失败时 baseline 保持不变，下一次运行仍会针对同一份源码变化重新处理。

`document_updater` 只处理一份 Markdown 和一个 `DocumentResult`。因此 Entry 到文档的映射、多个
producer 的分组、文件读写、审核和 baseline commit 都保留在调用方。示例中的对象职责如下：

| 对象 | 所有权与职责 |
| --- | --- |
| `affected_entries_by_form()` | 普通分析函数；按 Form 收集非空的受影响 `EntryChange`，不写入文件或推进 baseline |
| `publish_documents_for_forms()` | 普通发布函数；按 Form 生成并发布 Entry 文档，返回实际改变的 document keys |
| `generate_entry_document()` | 根据一个 `EntryChange` 读取 prompt 文件、依次调用 LLM，合并为一个 `DocumentResult` |
| `generate_ui_document()` | 非 LLM 生成接口；输入完整 UI `SourceFile`，输出组件表格的 `DocumentResult`；提取逻辑由调用方实现 |
| `LlmClient` | 稳定的文本生成适配器接口；具体模型、提示执行方式和 provider 可替换 |
| `DocumentPublisher` | 提供 `read_fragment(document_key, fragment_id)` 和 `publish(results_by_document)`；不依赖 Entry、UI 或 prompt |
| `FileTracker` | 维护源码 baseline；只在发布成功后推进 |

对每个 Entry，调用方先通过 `get_fragment_markdown()` 读取目标 fragment：

| 状态 | 调用方动作 |
| --- | --- |
| fragment 不存在 | 使用 bounded `new_context` 创建 prompt；省略 diff、旧代码和旧 fragment。 |
| fragment 存在 | 使用当前 fragment、当前代码和受限变更摘要创建 prompt。 |
| Entry 已删除 | 直接创建相同 `fragment_id` 的 `DocumentResult(..., DELETE)`。 |

小模型多角度分析同一个 Entry 时，调用方选择稳定有序的多个 `(system_prompt, user_prompt)` 对，
按序调用模型并合并 completion，最后仍创建唯一的 `UPSERT DocumentResult`。每个 prompt 应限制
输出长度，并负责不重叠的内容；fragment core 不包含模型、prompt 或视角概念。

## Entry 生成参考实现

```python
changes_by_form = affected_entries_by_form(source_root, change_set)
updated_document_keys = publish_documents_for_forms(
    changes_by_form,
    publisher,
    client,
)
```

默认从脚本旁的 `examples/prompts/` 读取文件，不依赖进程工作目录。调用方可以通过
`prompt_directory` 更换目录，在函数内直接修改有序的 `prompt_files` 选择规则：

1. `business_flow.system.txt` + `entry.user.template`：业务流程。
2. `boundaries.system.txt` + `entry.user.template`：边界与异常。

这是共享 user template 的两次独立调用，按顺序用空行连接输出，不自动把上一步结果传给下一步。
每个 pair 也可以使用不同的 user template；无需增加 prompt 对象或 pipeline。
模板支持 `{entry_id}`、`{code}`、`{history}`，模板本身的字面花括号需写成 `{{`、`}}`；
插入的源码不进行二次格式化。首次生成的 `history` 为空，更新时包含已有片段和函数变更摘要。

所有模板先读取并格式化，随后才调用 LLM。缺失文件、空 prompt、未知模板变量、模型异常、
任意一步的空回复或无效最终 `DocumentResult` 都明确失败，不用空内容冒充成功。
删除 Entry 时直接返回 `DELETE`，不读取模板、不调用模型。

## 非 LLM 生成与统一落盘

`generate_ui_document(source, *, fragment_id)` 只声明扩展边界，当前调用会抛出
`NotImplementedError`，不包含假的组件提取实现。UI 输入是完整源码，不依赖 `EntryChange`：
UI 变化可能没有关联入口，需要调用方独立选择待处理的 UI 文件。

实现该函数后，可将结果交给同一个发布流程：

```python
ui_result = generate_ui_document(source, fragment_id="ui:frmOrder:components")
document_key = "docs/frmOrder.md"
publisher.publish(document_key, (ui_result,))
```

调用方负责让这类额外结果对应本次源码快照，并以其 document key 调用同一个 publisher。
示例 `main()` 只演示 Entry 生命周期，不调用尚未实现的 UI 生成接口。
UI 源文件删除时，调用方直接创建相同 fragment ID 的 `DELETE`，无需把不存在的源码交给提取器。
两个生成函数只共享输出类型，不需要共同的基类。

调用方以 document key 聚合全部 producer 的结果。一个 key 的空结果列表表示文档参与本次运行但
没有 fragment 修改；mapping 的 key 集合就是本次涉及的文档。写入前验证同一文档不存在重复
fragment ID，随后按确定性顺序应用结果。只有生成、应用和物理发布均成功，才能用 `ImpactReport`
中的 revisions 推进 FileTracker baseline。
错误向调用方传播，失败不会提交 baseline；此示例不提供多文件原子发布，写入失败前可能已有部分
文档落盘。

完整可运行示例见
[`examples/documentation_lifecycle.py`](../examples/documentation_lifecycle.py)。
