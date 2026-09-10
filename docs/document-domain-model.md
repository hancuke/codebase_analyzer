# 文档元数据与按需更新领域模型

## 问题

代码分析能够确定哪些入口受到变更影响，但文档的组织单位不必等于入口。对 Access Form 而言，
同一窗体的 `bSave_Click`、`bCancel_Click` 等入口通常由一份窗体文档共同解释。因此，必须保存
“哪个文档覆盖哪些入口”的显式关系，才能把代码变更确定地转换成待更新文档，而不让调用方临时猜测
路径或让多个更新任务覆盖同一个文件。

## 公共语言和最小 primitives

| 名称 | 含义 | 身份 | 不变量 |
| --- | --- | --- | --- |
| `Entry` | 可被调用图识别并可能受影响的代码行为。 | 稳定 `entry_id`。 | 它是 CodeGraph / ChangeAnalyzer 的只读事实，不包含文档状态。 |
| `Document` | 供维护者查询和阅读的项目文档。 | 规范项目相对 POSIX `document_path`。 | 内容、哈希、生成器和发布状态不属于关系模型。 |
| `Coverage` | 一份文档明确覆盖的一组入口。 | `document_path` 与 `entry_ids`。 | 入口最多属于一份显式文档；一份文档必须覆盖至少一个入口。 |
| `DocumentCatalog` | 有版本、确定性的 `Coverage` 集合。 | 版本控制的 JSON 文件。 | 文档路径和入口归属均唯一；不引入额外数据库 ID。 |
| `DocumentUpdatePlan` | 一次变更后待处理的一份文档工作项。 | `document_path` 和本次工作快照。 | 每个受影响文档只有一项；保留每个受影响入口的证据。 |

`Coverage` 是唯一持久化的代码—文档关系。`DocumentCatalog` 是该关系的集合名，不是需要生命周期、
状态机或独立 ID 的新业务对象。

## 关系和清单

一份文档可以覆盖同一个 Form 的多个入口。一个入口不能同时归属两份显式文档，否则无法决定由哪一份
文档承接它的更新。清单存放在项目中并随代码评审、版本化，例如 `docs/document-registry.json`：

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
    },
    {
      "path": "docs/forms/customer.md",
      "entry_ids": [
        "vba:frmCustomer:bDelete_Click"
      ]
    }
  ]
}
```

路径必须是非空、规范的项目相对 POSIX 路径：不允许绝对路径、反斜杠、`.` 或 `..` 段。入口 ID 不可为空
或重复；一个入口归属多份文档、同一路径登记多次都是错误。没有显式登记的入口使用现有默认路径，并产生
不写回清单的隐式单入口 coverage，以保持现有调用方兼容。

## 按需更新流程

```text
完整源码快照
  -> CodeGraph 提取 Entry
  -> ChangeAnalyzer 比较旧/新图，产出 EntryImpact
  -> DocumentCatalog 解析 entry_id -> document_path
  -> 按 document_path 聚合受影响 EntryImpact
  -> DocumentUpdatePlan
  -> 调用方读取、生成、校验、发布文档
```

`EntryImpact` 仍是代码变更事实：它保留单一入口到变化函数的 old/new 路径。聚合阶段只将共享同一
`document_path` 的影响组成一份 `DocumentUpdatePlan`，并保留每个入口各自的 impact、evidence、
baseline context 和 working context。函数变化、调用边变化和 diagnostics 在文档计划级别稳定去重。

文档动作基于 catalog 覆盖的入口在旧、新代码图中的存在性确定：新出现的 coverage 为 `create`，完全
消失的 coverage 为 `archive`，其余为 `update`；相关分析存在 error diagnostic 时为 `review`。

例如 `SharedRule` 同时被 `frmOrder` 和 `frmCustomer` 调用时，变更会产生
`docs/forms/order.md` 和 `docs/forms/customer.md` 两份计划；`frmOrder` 的两个入口只会聚合到前者，
不会产生两份会互相覆盖的任务。

## 边界

`document_updater` 拥有 catalog 解析和计划聚合。`code_graph` 不读取文档或清单，`file_tracker` 不理解
调用图和 coverage；因此 workspace 依赖方向不变。本模型不读取或写入 Markdown 内容，不调用 LLM，
不记录内容哈希、最后更新时间、审批、失败重试或发布状态。这些应由 catalog 下游的发布流程拥有。
