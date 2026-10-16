# Document Workbench：成功结果与显式发布

工作台保存完整文档结果和不可变的任务片段结果，不保存输入、任务计划、revisions 或业务 metadata。
存储 schema 为 7；可恢复 schema 5，后续状态提交写为 7；schema 1/2/3/4/6 不兼容。
schema 6 的不透明文本任务须使用此前版本完成后再升级。

## 1. 成本边界与所有权

每次执行都重新扫描、分析源码，只有昂贵的文档生成按 document_key 跳过。
一个工作台对应同一批固定工作；恢复期间输入和配置不变，变化时由调用方清理并重开。
工作台不自动识别输入变化或证明生成内容正确。
这里的输入包括源码、原始文档和生成配置；调用方须保证整批执行及恢复期间不发生外部修改。
工作台不复制 document_root、不固定原文快照，也不检测外部编辑冲突；发布完整结果可能覆盖外部编辑。
调用方保证同一个 document_root 同时只由一个实例操作，不同实例的文档目录独立且互不嵌套。
工作台不加锁，也不检测目录或进程占用；违反单实例契约可能丢失完成记录、覆盖结果或干扰清理，
不保证报错。直接使用 API 的调用方也不得共享 workbench_root。
MVP 使用 document_root / ".workbench"，由文档目录的独占契约同时保证工作台独占；
文档扫描须排除该目录，发布 key 不得指向工作台。

调用方拥有扫描、分析、LLM 和全量完成判断。
工作台拥有成功结果存储、完整性校验、assemble 组装、显式发布到 document_root 及发布后的清理。
`tracker.commit()` 是额外的独立步骤，不属于发布，不由工作台调用。
工作台依赖 document_updater.DocumentResult 作为片段值类型，TaskResult 拥有检查点序列化与校验，
不依赖 FileTracker、CodeGraph 或 change_analyzer；assemble 调用 document_updater.apply_results，
不复制片段合并规则。

## 2. 接口

```python
class Workbench:
    @classmethod
    def open(
        cls, root: str | os.PathLike[str], *,
        protected_roots: Iterable[str | os.PathLike[str]] = (),
    ) -> Workbench: ...
    def contains(self, document_key: str) -> bool: ...
    def contains_task(self, document_key: str, task_id: str) -> bool: ...
    def save_task(self, document_key: str, task_id: str,
                  results: tuple[DocumentResult, ...]) -> None: ...
    def task_results(self, document_key: str) -> Mapping[str, tuple[DocumentResult, ...]]: ...
    def task_documents(self) -> tuple[str, ...]: ...
    def assemble(self, document_root: str | os.PathLike[str]) -> None: ...
    def save(self, document_key: str, content: str) -> None: ...
    def results(self) -> Mapping[str, str]: ...
    def publish(self, document_root: str | os.PathLike[str]) -> None: ...
    def close(self) -> None: ...
```

导出 Workbench、WorkbenchError 和不可变检查点 TaskResult，无 Batch、prepare 或 metadata。

- open 创建或恢复索引并校验成功结果，不持有文件锁，不检测并发打开。
  调用方可通过 protected_roots 声明受保护目录；内部解析实际路径，在创建或修改工作台前
  拒绝相同目录及双向包含关系。此参数不持久化，恢复时应再次传入。
- contains 查询并校验对应的成功结果，无记录返回 False，损坏则报错。
- save 将完整 UTF-8 文本保存为字节再提交成功索引，不要求注册任务，不允许覆盖成功 key。
  首次 save 自动持久化冻结任务集合，再写完整文档；无需显式 seal_tasks。
- results 返回按 key 排序的只读成功结果 mapping，不判断是否全部任务完成。
- publish 显式复制所有成功结果到 document_root，全部发布成功后关闭并清理工作台。
- close 仅使当前对象失效，不持有需要释放的资源，不自动发布或清理。
  无需 with 或 finally；有状态对象可跨函数传递。with 作为兼容便利接口保留，退出调用 close。

空文本合法；保存和读取都以 UTF-8 编码，非法 UTF-8 的已保存结果会明确报错。
调用方必须在全部处理成功后调用 publish；处理异常直接传播，不能吞掉异常后发布部分结果。

## 3. 存储与可靠提交

```text
document_root/
  .workbench/
    state.json
    outputs/docs/orders.md
```

```json
{
  "schema_version": 7,
  "tasks": {},
  "frozen": true,
  "completed": {
    "docs/orders.md": {"size_bytes": 123, "sha256": "..."}
  }
}
```

只有一个正式状态 JSON，保存成功索引、任务片段结果和 frozen 标记；完整文档结果单独保存。
key 为规范化相对 POSIX 路径，拒绝路径穿越、大小写及文件/目录碰撞。
状态和内部输出路径不能是符号链接，JSON 重复 key 明确失败。

save 先写结果并同步文件和目录，再通过临时 JSON、同步、原子替换及目录同步提交。
输出存在但索引尚未提交不算成功，下次允许重新生成和覆盖未提交输出。
JSON 替换后同步失败会使提交结果不确定，当前句柄禁止继续操作，需关闭重开。
不承诺生成 exactly-once；生成完成但保存失败时可能再次生成。

打开、查询和交付时完整性校验对调用方透明，损坏不默默变成空索引。
新工作台不创建 .lock。此前 schema 5 的普通 .lock 文件仍被接受并在发布后清理，
但不用于互斥；不应与旧版程序同时访问同一工作台。
没有 O_DIRECTORY 的平台不保证相同的目录同步持久性。

## 4. 发布与清理

publish 内部先校验所有结果，再逐文件写目标目录的临时文件、同步并原子替换。
不逐文件 move；发布过程中所有源结果保留在工作台，发布中断可重复执行，
无需重新生成已成功文档。目标目录不能在工作台内，不跟随目标内部符号链接。

全部目标文件写入成功后，关闭句柄并删除本批已知的结果、目录和状态。
不递归删除未知数据；未知残留使清理明确失败。
publish 成功后实例已关闭，不能继续 save、contains、results 或 publish；close 为幂等操作。
清理失败可能留下残缺检查点，必须明确恢复或完成清理，不能假装发布未发生。

单文件原子替换不等于多文件同时可见。发布不与 baseline 提交构成事务：
如果调用方后来执行 tracker.commit 失败，文档已经发布、工作台也可能已经清理。
调用方必须独立管理该业务错误，工作台不保存 baseline 状态。
调用方需独占本批及目标目录，包括生成、等待确认、恢复、发布及清理阶段。

## 5. 示例主流程

```python
workbench_root = document_root / ".workbench"
workbench = Workbench.open(workbench_root, protected_roots=(source_root,))
changes = tracker.scan()
for key, function_changes in analyze_documents(source_root, changes).items():
    if workbench.contains(key):
        continue
    markdown = read_document(document_root / key)
    results = generate_fragments(function_changes)
    markdown = apply_results(markdown, results)
    workbench.save(key, markdown)

# 可以将 workbench 返回给上层，在确认全部结果后发布。
workbench.publish(document_root)

# 如业务需要，调用方另行执行 tracker.commit(...)，不属于以上流程。
```

MVP 内的 analyze_documents 和 read_document 分别负责分析分组和读取原文，
目录保护由 open 执行。示例临时项目仅用于演示，实际恢复需持久目录。

MVP 对每份未完成文档读取一次原文。generate_fragments 只生成 DocumentResult 片段结果，
不修改整文；prepare_batch 使用 document_updater.apply_results 批量更新目标片段，再将完整文本交给
save 并返回 Workbench。可注入的 generate 回调可以接收原文作为上下文，但同样只返回片段结果，
统一由 prepare_batch 合并。run_batch 是准备后立即发布的便利入口，无需上下文管理器。
保存完整文档不意味着重新生成其他片段。人工文本和未修改片段按现有片段渲染规则保留，
删除最后一个片段后若结果为空，则发布为空文件。其他示例的 DocumentPublisher 直接发布流程保持独立。

生成中断时，正式文档尚未修改；恢复时重新分析并跳过已保存的文档。部分发布失败时，
正式目录可能已有本次发布的内容，但全部生成结果已保存，重试只重复发布，不读取这些文档重新生成。
发布自身的修改不属于上述禁止的外部输入修改。

## 6. A/B 阶段与任务项恢复

Workbench 以 `(document_key, task_id)` 为任务检查点标识，保存 `tuple[DocumentResult, ...]`。
TaskResult(document_key, task_id, results) 封装组合标识与结果 tuple，
通过 to_dict()/from_dict() 负责序列化和校验，不修改 DocumentResult；
Workbench 负责原子持久化，assemble 协调原文读取和 apply_results 调用。
任务 ID 不依赖运行次序或位置；一个任务项对应一份文档，可以返回零个或多个片段。
一个阶段包含多个任务项，A 和 B 可以为同一文档贡献不同片段。

```python
workbench = Workbench.open(document_root / ".workbench", protected_roots=(source_root,))
for task in tasks_a + tasks_b:
    if workbench.contains_task(task.document_key, task.id):
        continue
    results = generate(task)  # 失败不提交该任务；已完成任务仍可恢复
    workbench.save_task(task.document_key, task.id, results)

# 调用方确认 A、B 全部完成；工作台不推断阶段完整性。
workbench.publish(document_root)
```

save_task 将任务的全部片段结果作为 JSON 数组原子提交到 state.json，不将整份文档标记完成。
重复提交同一 document_key/task_id 明确报错；不同文档可以复用 task_id；
重跑先 contains_task。结果保存失败时可能需要重新生成，中断后重新打开检查持久化记录。
任务内容不向正式目录发布。task_results 返回按任务保存顺序排列的只读 mapping，值为结果 tuple；
未知文档返回空 mapping；task_documents 返回排序的任务文档 key tuple。
空 tuple 也是合法任务完成结果；非 tuple 或非 DocumentResult 元素明确报错，不提交任务。
源码、原始文档、任务 ID 对应的含义和生成配置在本批及恢复期间必须不变。

调用方确认全部阶段完成后，按单个文档的任务保存顺序、任务内部片段顺序确定性合并，恢复后保留顺序。
首次保存完整文档时，Workbench 首先持久化 frozen 标记，再写文档输出。同一 fragment_id 按顺序执行，
后保存的任务覆盖之前的内容；更新已有片段保留原位置，新片段或删除后重插的片段按应用顺序追加。
顺序依赖 state.json 的成员顺序，调用方不得重排 JSON 或用旧版本重写状态；
旧状态文件只能沿用已保存的任务 ID 字典序，无法还原历史保存顺序。
assemble 对每份尚未保存的文档只读取一次原文，再调用 apply_results，复用 save 保存完整文本。
不存在的原文作为空文档；读取或合并错误传播，不将失败文档标记完成。
组装不写正式文档，拒绝符号链接、非普通文件及指向工作台的文档路径。
save 失败后可重开并再次合并保存，跳过已完成文档；冻结后禁止继续 save_task。
publish 自动调用 assemble 组装尚未保存的任务文档，全部组装成功后才开始发布。
组装失败不发布任何文档；发布失败后重试复用已保存结果，不重读或重合并原文。
仍可独立调用 assemble 以检查完整结果；publish 不自动推断任务或阶段是否齐全。

仅使用 document_root / ".workbench" 保存任务和完整文档检查点；文档扫描必须排除它，
文档 key 不能指向或覆盖工作台。不存在额外会话对象、任务目录或显式冻结调用。
发布失败保留两类检查点，重试复用完整文档，不再次读取原文或生成片段。
发布成功后统一清理任务文本和文档存储；清理失败明确传播，可能需要人工完成。
工作台不会调用 tracker.commit。

MVP 的 main 演示 A 中断、重新打开并完成 A、B 为同一文档增加摘要，最后 save 和 publish。
prepare_batch / run_batch 仍保留为单阶段、完整文档检查点的便利入口。

## 7. 验收

生成失败重跑再次分析并跳过成功 key；跨进程恢复；UTF-8 文本往返及非法编码拒绝；
提交故障窗口与损坏报错；路径保护；显式对象跨函数发布；关闭不自动发布；
显式发布成功后清理；发布失败保留检查点和旧目标文件；发布不调用 tracker.commit。
任务项中断恢复；A/B 片段合并；稳定任务排序与重复片段行为；冻结后拒绝添加；
空任务完成记录；任务保存失败重试；部分发布失败不重新合并；任务与文档检查点清理。
