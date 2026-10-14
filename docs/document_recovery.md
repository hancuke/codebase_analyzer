# Document Workbench：成功结果与显式发布

工作台只保存成功结果，不保存输入、任务计划、revisions 或业务 metadata。
存储 schema 为 4；旧 API 和 schema 1/2/3 不兼容，不自动迁移。

## 1. 成本边界与所有权

每次执行都重新扫描、分析源码，只有昂贵的文档生成按 document_key 跳过。
一个工作台对应同一批固定工作；恢复期间输入和配置不变，变化时由调用方清理并重开。
工作台不自动识别输入变化或证明生成内容正确。

调用方拥有扫描、分析、LLM、文档合并和全量完成判断。
工作台拥有成功结果存储、完整性校验、显式发布到 document_root 及发布后的清理。
`tracker.commit()` 是额外的独立步骤，不属于发布，不由工作台调用。
工作台不依赖 FileTracker、CodeGraph、change_analyzer 或 document_updater。

## 2. 接口

```python
class Workbench:
    @classmethod
    def open(
        cls, root: str | os.PathLike[str], *,
        protected_roots: Iterable[str | os.PathLike[str]] = (),
    ) -> Workbench: ...
    def contains(self, document_key: str) -> bool: ...
    def save(self, document_key: str, content: bytes) -> None: ...
    def results(self) -> Mapping[str, bytes]: ...
    def publish(self, document_root: str | os.PathLike[str]) -> None: ...
    def close(self) -> None: ...
```

仅导出 Workbench 和 WorkbenchError，无 Batch、prepare、metadata 或状态对象。

- open 创建或恢复索引，校验成功结果并持有操作系统文件锁；并发打开立即报错。
  调用方可通过 protected_roots 声明受保护目录；内部解析实际路径，在创建或修改工作台前
  拒绝相同目录及双向包含关系。此参数不持久化，恢复时应再次传入。
- contains 查询并校验对应的成功结果，无记录返回 False，损坏则报错。
- save 保存完整 bytes 再提交成功索引，不要求注册任务，不允许覆盖成功 key。
- results 返回按 key 排序的只读成功结果 mapping，不判断是否全部任务完成。
- publish 显式复制所有成功结果到 document_root，全部发布成功后关闭并清理工作台。
- close / with 退出只释放锁。正常退出也不自动发布，不自动清理。

空 bytes、非 UTF-8 bytes 合法，文本编码由调用方负责。调用方必须在全部处理成功后调用
publish；处理异常直接传播，不能吞掉异常后发布部分结果。

## 3. 存储与可靠提交

```text
workbench/
  .lock
  state.json
  outputs/docs/orders.md
```

```json
{
  "schema_version": 4,
  "completed": {
    "docs/orders.md": {"size_bytes": 123, "sha256": "..."}
  }
}
```

只有一个正式状态 JSON，保存成功索引；完整结果单独保存。
key 为规范化相对 POSIX 路径，拒绝路径穿越、大小写及文件/目录碰撞。
状态和内部输出路径不能是符号链接，JSON 重复 key 明确失败。

save 先写结果并同步文件和目录，再通过临时 JSON、同步、原子替换及目录同步提交。
输出存在但索引尚未提交不算成功，下次允许重新生成和覆盖未提交输出。
JSON 替换后同步失败会使提交结果不确定，当前句柄禁止继续操作，需关闭重开。
不承诺生成 exactly-once；生成完成但保存失败时可能再次生成。

打开、查询和交付时完整性校验对调用方透明，损坏不默默变成空索引。
锁文件存在不表示被占用；Windows/Unix 使用操作系统锁。
没有 O_DIRECTORY 的平台不保证相同的目录同步持久性。

## 4. 发布与清理

publish 内部先校验所有结果，再逐文件写目标目录的临时文件、同步并原子替换。
不逐文件 move；发布过程中所有源结果保留在工作台，发布中断可重复执行，
无需重新生成已成功文档。目标目录不能在工作台内，不跟随目标内部符号链接。

全部目标文件写入成功后，关闭句柄并删除本批已知的结果、目录和状态。
不递归删除未知数据；未知残留使清理明确失败。
publish 成功后实例已关闭，with 随后的退出为幂等关闭。
清理失败可能留下残缺检查点，必须明确恢复或完成清理，不能假装发布未发生。

单文件原子替换不等于多文件同时可见。发布不与 baseline 提交构成事务：
如果调用方后来执行 tracker.commit 失败，文档已经发布、工作台也可能已经清理。
调用方必须独立管理该业务错误，工作台不保存 baseline 状态。
调用方需独占本批及目标目录，包括关闭锁后的清理阶段。

## 5. 示例主流程

```python
with Workbench.open(workbench_root, protected_roots=(source_root,)) as workbench:
    changes = tracker.scan()
    for key, function_changes in analyze_documents(source_root, changes).items():
        if workbench.contains(key):
            continue
        content = generate_document(function_changes, read_document(document_root / key))
        workbench.save(key, content)
    workbench.publish(document_root)

# 如业务需要，调用方另行执行 tracker.commit(...)，不属于以上流程。
```

示例支持模块只负责分析分组和读取原文，目录保护内化到 open，不再提供 publish_batch、
revision 比较或 baseline 提交。示例临时项目仅用于演示，实际恢复需持久目录。

## 6. 验收

生成失败重跑再次分析并跳过成功 key；跨进程恢复；二进制结果往返；
提交故障窗口与损坏报错；路径和并发保护；正常退出不自动发布；
显式发布成功后清理；发布失败保留检查点和旧目标文件；发布不调用 tracker.commit。
