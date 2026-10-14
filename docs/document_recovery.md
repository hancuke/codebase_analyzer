# 文档更新运行：中断恢复

## 目标与领域边界

源码分析便宜，文档生成昂贵。恢复时可以重新分析，但不应重新生成已经完整保存的文档。

`document_workbench.DocumentRun` 保存一次运行的固定输入、文档单元计划和完成结果。它不扫描源码，不调用分析器或 LLM，也不发布文档或推进 FileTracker baseline。这些工作由调用方完成。

**一个恢复单元就是一份目标文档的完整更新。** 同一文档的所有 fragment 新增、修改和删除必须合并为完整结果，才能提交完成记录。单元 ID 是调用方确定的规范化相对文档路径，例如 `docs/orders.md`。共享变化可以影响多个文档，不改变分析领域的多对多关系。

## 固定输入目录 + manifest 最后提交

```text
run/
├── .lock
├── inputs/
│   └── snapshot.json
├── outputs/
│   └── docs/
│       ├── A.md
│       └── B.md
└── manifest.json
```

`snapshot.json` 是示例适配器的文件布局，不是 DocumentRun 的固定要求。调用方可以在 `inputs/` 保存多份文件；DocumentRun 记录它们的大小和摘要。

**manifest 是提交依据，文件存在本身不表示完成。**

首次运行在持有运行锁时：

1. 调用 `capture_inputs(inputs_root)`，直接把完整输入写入 `inputs/`。
2. 校验输入，记录文件清单和摘要，并持久化文件与目录。
3. 最后原子写入 `manifest.json`，输入快照才正式生效。

不需要额外的输入 staging 目录。若进程在步骤 3 前退出，下次初始化发现没有 manifest，会删除专用的未提交 `inputs/` 和 `.manifest-*.tmp` 文件，然后重新捕获。只清理这些保留路径；未知文件、非法目录或符号链接会明确报错。

已有 manifest 时，只打开并校验已提交运行，不重新采集，也不清理输入。损坏或不兼容的 manifest、输入或已完成结果会明确失败，不能通过重新扫描实时文件来补齐。

## 文档生成与恢复

```text
固定输入提交
  -> 调用方分析固定输入并按目标文档分组
  -> 提交排序后的文档单元 ID 计划
  -> 生成一个待处理文档的完整结果
  -> 保存并持久化输出
  -> 原子提交该文档的完成记录
```

计划尚未注册与空计划不同；空计划表示本次没有文档需要生成。恢复时重新推导的单元 ID 必须与原计划相同，否则报错。

待处理文档由以下规则确定：

```text
待处理文档 = 已注册计划 − 已完成记录
```

例如计划包含 A、B，A 已完成，生成 B 时中断。恢复后复用固定输入，重新执行便宜的分析，校验 A 的已保存结果并跳过生成，只重新生成 B。B 的半成品输出会在重试前删除。

若输出已保存，但完成记录尚未提交，该文档仍然待处理。允许重新生成，不承诺 LLM 调用 exactly-once，也不恢复到某个 prompt 或 fragment 内部。`COMPLETED` 只表示所有计划内文档结果已保存，不表示正式文档已发布。

## 公共接口

| 接口 | 语义 |
| --- | --- |
| `DocumentRun.open_or_create(root, capture_inputs=...)` | 持锁打开已有运行，或捕获输入并初始化；恢复时不执行回调 |
| `DocumentRun.open(root)` | 只打开已有有效运行 |
| `snapshot_root` | 读取已提交的固定输入；提交后调用方不得修改 |
| `register_units(unit_ids)` | 注册计划；相同计划可重复注册，不同计划报错 |
| `pending_units()` | 校验输入和完成输出，返回排序后的待处理 ID |
| `status()` | 查询阶段、计划和完成记录 |
| `prepare_unit_output(unit_id)` | 为待处理文档准备路径，删除该文档上次未提交的输出 |
| `mark_unit_completed(unit_id)` | 校验并持久化完整输出，再原子提交完成记录 |
| `output_path(unit_id)` | 返回校验通过的已完成文档输出 |

manifest 更新使用临时文件写入、文件同步、原子替换和目录同步。运行锁拒绝同一目录的并发处理；不存在额外的任务调度器、分析缓存或存储后端抽象。

Windows 提交输入或输出时，需要以非截断读写模式打开文件才能同步到磁盘，因此这些文件必须可写；普通摘要校验仍只读。目录同步仅在平台提供 `O_DIRECTORY` 时执行，Windows 不提供同等的目录持久化保证。文件同步失败会传播，不会将未同步结果标记为完成。

## 调用方示意

下面的 capture/load/analyze/generate 函数由调用方实现；完整可运行示例见 `examples/document_workbench_mvp.py`。该示例在主流程中初始化固定输入，处理函数只打开已有运行；恢复处理无需再次传入采集回调。

```python
from document_workbench import DocumentRun

with DocumentRun.open_or_create(
    run_root,
    capture_inputs=capture_inputs,
) as run:
    pass

with DocumentRun.open(run_root) as run:
    snapshot = load_snapshot(run.snapshot_root)
    # 每次从固定输入重算，分析结果按目标文档确定性分组。
    changes_by_document = analyze_and_group(snapshot)
    run.register_units(changes_by_document)

    for document_id in run.pending_units():
        markdown = generate_complete_document(
            snapshot,
            document_id,
            changes_by_document[document_id],
        )
        output = run.prepare_unit_output(document_id)
        output.write_text(markdown, encoding="utf-8")
        run.mark_unit_completed(document_id)
```

FileTracker 的固定 ChangeSet、完整源码、旧文档以及影响生成的必要配置由适配器负责捕获和编解码。分析和生成只能使用本次固定输入，不应恢复时回读已变化的实时源码或旧文档。DocumentRun 不接收 `ChangeSet`、`SourceFile` 或 `DocumentResult` 等其他包的领域对象。

## 发布与 baseline

完成结果先保存在运行目录，审核和发布仍由调用方负责。发布可能在中断后重复执行，调用方应保证重复写入同一结果是安全的，并处理正式文档的并发修改。

只有所有文档发布成功且原始 ChangeSet revisions 仍匹配，才能推进 FileTracker baseline。DocumentRun 的完成记录不替代发布状态，也不自动提供跨文档发布事务。
