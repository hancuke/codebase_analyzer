# Access 文档 CLI

与同目录 PL/SQL 示例使用相同方式：业务字段在 `access_doc_cli/cli.py` 的普通 dataclass
`RunOptions` 中声明，复用 `plsql_doc_cli/validation.py` 通用加载器，不修改领域包。

```bash
# 实际应用项目中
uv run access-docs run --config configs/access-rms-prod.toml
# 默认读取当前目录 access-docs.toml
uv run access-docs run
```

Access 命令只接受 `--config`（及内置 help），不提供路径覆盖、模式或额外输出选项。

## 复制到实际项目

复制 `src/access_doc_cli/` 及 `src/plsql_doc_cli/validation.py`（保留对应包的
`__init__.py`），或将通用校验器移入目标项目的公共模块并修改导入。无需复制 PL/SQL 的
`cli.py`。实际项目提供 `processor.AccessConfig`、`processor.AccessService`、Workbench
及 infra 日志和 Phoenix tracing。

合并脚本注册到现有 `pyproject.toml`，确保包发现包含 CLI 模块：

```toml
[project.scripts]
access-docs = "access_doc_cli.cli:main"
```

Python 3.10 需合并 `"tomli>=2; python_version < '3.11'"` 到依赖；之后在目标项目 `uv sync`。
不要覆盖实际项目的 pyproject，也不要复制本例独立 workspace 的隔离配置。

## 配置

完整用户路径见 [`configs/access-rms-prod.toml`](configs/access-rms-prod.toml)。
Windows 反斜杠路径使用 TOML 单引号，避免转义；以下为相对路径版本：

```toml
[variables]
prompt_root = "../prompts/access"

[project]
system_id = "rms"
env_id = "prod"

[paths]
source_root = "../workspace/rmsdev"
document_root = "../specs/access"
log_file = "../logs/form.log"
lookup_ref_csv_path = "../lookup_ref1.csv"
table_file_folder_path = "../schema/tables_views"

[prompts]
sys_prompt_directory = "${prompt_root}"
ui_user_prompt_directory = "${prompt_root}/ui_user_prompts"
vba_user_prompt_directory = "${prompt_root}/vba_user_prompts"
```

所有业务字段必填。源码、lookup CSV、表结构路径、三个 Prompt 路径均声明
`must_exist=True`；文档目录和日志文件允许尚不存在。只检查路径存在性，不额外校验文件类型、
CSV 内容或 Prompt 格式，这些属于原服务。相对路径基于配置文件目录，变量只展开一次，
不读取环境变量；通用加载规则详见 [README](README.md)。

## 固定流程与错误

1. 加载和校验 TOML，初始化日志、AccessConfig、Workbench 和 AccessService。
2. 初始化 tracing，调用 `analyze_form_ui()`，获得 `form_lookup_mapping` 和 UI 错误标志。
3. 将同一个 mapping 原样传给 `analyze_entrys()`；方法拼写保留原服务 API。
4. 累计两个错误标志；全部成功才 publish 一次，成功后记录日志。
5. tracing 初始化成功后，无论生成/发布结果如何，都通过 finally 尝试 shutdown。

返回 0 表示分析和发布成功，1 表示任一分析阶段返回错误标志而未发布，2 表示参数或配置错误。
UI 正常返回错误标志时仍运行入口分析；异常则立即停止并传播，不发布。UI 失败时 mapping
是否可供入口分析使用须在真实服务确认。无效的非 bool 错误标志明确抛 TypeError。
失败时不主动删除检查点、不推进 baseline、不承诺发布事务或自动回滚。
Workbench 位于 `document_root/.workbench`，按原接口保护源码目录；不并发共用 Workbench。

本仓库没有真实 processor/infra，可以验证 help、配置和替身测试，但完整运行会明确报缺失依赖：

```bash
uv run --project examples/plsql_cli access-docs run --help
uv run --project examples/plsql_cli pytest examples/plsql_cli/tests/test_access_cli.py -q
```

复制后仍需真实项目验收 UI mapping、入口分析、恢复检查点和发布行为。
