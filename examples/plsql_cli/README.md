# PL/SQL 文档 CLI 示例

同一示例包也提供 `access-docs`，复用通用校验器、独立维护 Access 运行字段和两阶段流程，
详见 [Access CLI](ACCESS.md)。

项目参数只通过 `--config` 选择，**路径、系统标识、日志位置、Prompt 根目录和匹配规则全部写在 TOML**，
不支持命令行覆盖，也没有 `config.py`。

```bash
uv run plsql-docs run --config configs/rms-prod.toml
# 默认读取当前目录的 plsql-docs.toml
uv run plsql-docs run
# CLI 专属枚举：成功后显示发布、源码及日志路径，不改变生成行为
uv run plsql-docs run --config configs/rms-prod.toml --detail full
```

每次固定顺序调用 `generate()` 和 `generate_special_package()`，两个阶段均成功后统一发布。
这里的“全量运行”指始终执行完整的两阶段流程，不会绕过服务原有的过滤、增量或检查点机制。

## 接入实际项目

本仓库没有原程序的 `processor`、`infra` 或真实 LLM/Phoenix 封装。本目录是可复制的独立
应用包骨架，不加入上层 uv workspace，也不实现这些外部服务。

1. 把 `src/plsql_doc_cli/` 复制到实际项目可打包的源码目录。
2. 复制并修改 [`configs/rms-prod.toml`](configs/rms-prod.toml)。
3. 在实际项目现有 `pyproject.toml` 中合并命令注册；Python 3.10 还需在已有 dependencies
   中添加 `"tomli>=2; python_version < '3.11'"`。不要覆盖原文件。

```toml
[project.scripts]
plsql-docs = "plsql_doc_cli.cli:main"
```

实际项目须已有 build-system 和正确的包发现配置；本目录的 `pyproject.toml` 提供
setuptools/src-layout 示例。目标项目继续负责安装 processor/infra，不需要复制本例用于
隔离的 `[tool.uv.workspace]`。在目标项目根目录 `uv sync` 后即可使用上述命令。

本仓库只需以下命令查看帮助、运行替身测试和构建：

```bash
uv run --project examples/plsql_cli plsql-docs run --help
uv run --project examples/plsql_cli pytest examples/plsql_cli/tests -q
uv build --project examples/plsql_cli
```

完整生成必须在真实项目执行；缺少依赖会明确失败，不会返回假的成功结果。

## 配置

```toml
[variables]
prompt_root = "../prompts"

[project]
system_id = "rms"
env_id = "prod"

[paths]
source_root = "../workspace/stored_proc"
document_root = "../workspace/specs/pkg"
log_file = "../logs/plsql.log"

[prompts]
sys_prompt_directory = "${prompt_root}"
simple_user_prompt_directory = "${prompt_root}/simple_user_prompts"
complex_user_prompt_directory = "${prompt_root}/complex_user_prompts"
special_user_prompts = "${prompt_root}/special_user_prompts"

[patterns]
exclude = ["pkg_schd_0*", "PKG_SCHD_0*"]
include_special = ["pkg_schd_0*", "PKG_SCHD_0*"]
```

业务字段均必填，不在代码中隐藏项目路径或业务匹配规则。不同环境维护不同 TOML，
通过 `--config` 选择。未知字段、类型错误、空字符串及无效输入目录会在服务初始化前报错。

所有相对路径以**配置文件所在目录**为基准。例如 `configs/rms-prod.toml` 的 `../prompts`
指项目根目录的 `prompts`。模板中的 Windows 绝对路径只能在 Windows 使用；
Linux/macOS 需改成本机路径。不在文件中放密钥。

TOML 本身没有变量语法；本例加载器使用标准库 `string.Template` 为 **Path 字段**增加
`${变量名}` 前缀引用。变量只来自可选的 `[variables]` 表，值必须为非空字符串，不读取
环境变量、不递归展开。未定义的变量或非法占位符会明确报错；路径中需要字面量 `$` 时写
`$$`。展开后的相对路径仍基于配置文件目录。也可以省略 `[variables]`，直接配置四个独立的
绝对或相对 Prompt 路径，目录名字不必与示例相同。

`patterns.exclude` 仅传给普通阶段 `exclude_pattern`；`patterns.include_special` 仅传给
特殊阶段 `include_pattern`，不自动同步。两组保留原始大小写和顺序，实际匹配规则由原服务
负责。允许 `exclude = []`，不允许空的 `include_special`，避免特殊阶段空规则的歧义。

## 代码维护的内部行为

`cli.py` 面向调用者，拥有 `RunOptions` 和两阶段编排，不再拼接 Prompt 子目录。
`validation.py` 是通用 TOML 加载与核心校验模块，不包含 PL/SQL 业务字段。
参数规则集中在 `cli.py` 的 `RunOptions` 字段声明中，例如：

```python
source_root: Path = field(metadata={"section": "paths", "must_exist": True})
document_root: Path = field(metadata={"section": "paths"})
system_id: str = field(metadata={"section": "project"})
exclude: tuple[str, ...] = field(metadata={"section": "patterns"})
# 有默认值的字段放在必填字段后面；配置可省略此项
env_id: str = field(default="prod", metadata={"section": "project"})
```

直接使用标准库 `dataclasses.field`：类型从 `str`、`Path`、`tuple[str, ...]` 注解推导，
不再重复声明 `Rule`、`kind` 或自定义 `setting()`。无 `default` / `default_factory`
表示必填，有默认值则配置可省略。默认值由代码提供，遵循字段声明类型。
`metadata["section"]` 指定 TOML 分组；`must_exist=True` 只对 Path 做预先存在检查。
未标记的 Path 允许不存在，不区分文件和目录。列表默认允许空，用 `nonempty=True`
声明非空列表（如 include_special）。字符串及列表中的字符串仍须非空。
路径按配置文件目录解析，TOML 数组转换为不可变 tuple。

新增同类参数时，在 `RunOptions` 增加一行声明，再更新 TOML 模板和使用该参数的业务调用，
不需要另改字段白名单、必填检查或加载映射。路径存在性由字段旁的标记统一控制，
不再单独枚举检查 Prompt 子目录或跨路径重叠。
新增不同类型时才扩展加载器的类型支持，不引入第三方配置框架。

通用接口为 `load_config(config_path, model)`：调用方传入声明了 section metadata 的 dataclass，
加载器校验 TOML 结构、必填值、类型及单个路径，返回对应模型实例。它不调用业务校验、
不创建目录，也不吞掉模型构造中的意外异常。其他 CLI 可以传入自己的模型，无需修改
`validation.py`。本例的 `load_options()` 直接返回加载结果。

### CLI 专属枚举字段

业务枚举在 `cli.py` 中定义，`argparse choices` 校验字符串。`RunOptions` 使用普通
`@dataclass`，入口加载配置后直接赋值：

```python
options = load_options(args.config)
options.detail = Detail(args.detail)
```

只在入口完成本次参数赋值，随后交给 `run(options)`；dataclass 赋值本身不会自动校验类型，
枚举转换由 `Detail(args.detail)` 负责：

```python
class Detail(str, Enum):
    SUMMARY = "summary"
    FULL = "full"

# RunOptions 内，CLI 专属字段必须有默认值，不需要 section
detail: Detail = field(default=Detail.SUMMARY, metadata={"cli": True}, kw_only=True)
```

`kw_only=True` 表示该字段只能通过关键字初始化，也避免继承模型新增必填字段时与默认字段
产生顺序冲突。通用加载器跳过 `cli=True` 的字段，使用其 default/default_factory；
不能在 TOML 中配置这些字段。枚举规则仍属于 CLI，不泄漏到 `validation.py`。
`--detail` 可选 `summary`（默认，保持现有输出）或 `full`（成功后显示路径）；
非法值由 argparse 自动报错并以退出码 2 终止，不加载配置、不初始化服务。

四个 Prompt 路径均在 `[prompts]` 中配置，不增加 CLI 覆盖选项。模板中的布局仅是示例：

```text
prompts/                       # sys_prompt_directory
  simple_user_prompts/
  complex_user_prompts/
  special_user_prompts/
```

源码和四个 Prompt 路径均标记为必须存在；Prompt 文件内容仍由真实服务负责检查。
Workbench 固定为 `document_root/.workbench`。输出目录可以尚不存在，日志父目录在运行时创建。
CLI 不再检查源码、文档和 Workbench 的重叠关系；Workbench 仍按原接口接收 protected_roots，
其他路径布局由调用者保证正确。
tracing 始终按原脚本开启，成功初始化后通过 finally 关闭。

返回 0 表示两阶段成功且 publish 返回；返回 1 表示生成返回错误标志，不发布、不主动清理
检查点；返回 2 表示 CLI/TOML 输入错误。普通阶段返回 `True` 仍继续特殊阶段，累计错误。
未预期异常立即停止并传播 traceback；不将 None 等无效服务返回当成成功。shutdown 自身失败
也会非零退出，原异常保留在 Python 异常链中。

不额外推进 FileTracker baseline，不实现发布回滚，也不承诺跨文件原子发布。Workbench
按原脚本的 API 使用，不假定支持 context manager/close，不允许并行命令共用同一 Workbench。
真实项目仍须验证两阶段任务 ID、失败恢复、修改配置后旧检查点的适用性，以及服务是否自行
推进 baseline。替身测试不能替代真实生成、恢复和发布集成验收。
