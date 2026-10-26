# PL/SQL 文档生成使用指南

`plsql-docs` 生成普通和特殊包文档，两个阶段均成功后发布结果。
Access 文档生成见 [Access 使用指南](ACCESS.md)。

以下命令在已安装此工具及生成服务的应用项目中运行。本仓库仅提供示例，不能独立完成真实生成。

## 使用配置文件（推荐）

复制 [配置模板](configs/rms-prod.toml)，将路径和项目标识改为自己的环境，然后运行：

```bash
uv run plsql-docs run --config configs/rms-prod.toml
```

需要在成功后显示文档、源码和日志位置时，添加 `--detail full`：

```bash
uv run plsql-docs run --config configs/rms-prod.toml --detail full
```

`--detail` 默认是 `summary`，只影响完成提示，不改变生成结果。查看全部参数：

```bash
uv run plsql-docs run --help
```

## 不使用配置文件

不传 `--config` 时，必须完整提供下面的业务参数；不会自动读取默认配置文件。
**不能将 `--config` 与业务参数混用，也不支持命令行覆盖配置文件。**

### Linux Bash

```bash
uv run plsql-docs run \
  --source-root /srv/project/source \
  --document-root /srv/project/docs \
  --log-file /srv/project/logs/plsql.log \
  --sys-prompt-directory /srv/project/prompts \
  --simple-user-prompt-directory /srv/project/prompts/simple_user_prompts \
  --complex-user-prompt-directory /srv/project/prompts/complex_user_prompts \
  --special-user-prompts /srv/project/prompts/special_user_prompts \
  --system-id rms --env-id prod \
  --exclude 'normal_*' 'NORMAL_*' \
  --include-special 'special_*' 'SPECIAL_*'
```

### Windows CMD

```bat
uv run plsql-docs run ^
  --source-root "C:\project\source" ^
  --document-root "C:\project\docs" ^
  --log-file "C:\project\logs\plsql.log" ^
  --sys-prompt-directory "C:\project\prompts" ^
  --simple-user-prompt-directory "C:\project\prompts\simple_user_prompts" ^
  --complex-user-prompt-directory "C:\project\prompts\complex_user_prompts" ^
  --special-user-prompts "C:\project\prompts\special_user_prompts" ^
  --system-id rms --env-id prod ^
  --exclude "normal_*" "NORMAL_*" ^
  --include-special "special_*" "SPECIAL_*"
```

Linux 用 `\` 续行，CMD 用 `^`，PowerShell 用反引号（`` ` ``）；续行符后不要加空格。
也可以把命令写在一行并去掉续行符。CMD 使用双引号，不能用单引号包住参数。
命令行的 Windows 反斜杠无需双写，带引号的目录路径建议省略末尾反斜杠。

## 配置内容

以下为 Linux 配置；所有字段都需填写，四个 Prompt 路径可以分别指定：

```toml
[variables]
prompt_root = "/srv/project/prompts"

[project]
system_id = "rms"
env_id = "prod"

[paths]
source_root = "/srv/project/source"
document_root = "/srv/project/docs"
log_file = "/srv/project/logs/plsql.log"

[prompts]
sys_prompt_directory = "${prompt_root}"
simple_user_prompt_directory = "${prompt_root}/simple_user_prompts"
complex_user_prompt_directory = "${prompt_root}/complex_user_prompts"
special_user_prompts = "${prompt_root}/special_user_prompts"

[patterns]
exclude = ["normal_*", "NORMAL_*"]
include_special = ["special_*", "SPECIAL_*"]
```

| 配置项 | 用途 |
| --- | --- |
| `system_id` / `env_id` | 系统和环境标识 |
| `source_root` | 待分析源码位置 |
| `document_root` | 文档输出位置 |
| `log_file` | 运行日志文件 |
| `sys_prompt_directory` | 系统 Prompt 位置 |
| `simple_user_prompt_directory` | 简单处理 Prompt 位置 |
| `complex_user_prompt_directory` | 复杂处理 Prompt 位置 |
| `special_user_prompts` | 特殊包 Prompt 位置 |
| `exclude` | 普通阶段排除的匹配规则 |
| `include_special` | 特殊阶段包含的匹配规则 |

`exclude` 可以是 `[]`，命令行对应显式传入 `--exclude` 而不带值；不能省略这个选项。
`include_special` 至少需要一个值。两组规则独立填写，保留大小写和顺序；
命令行中的匹配规则请加引号，避免被 shell 展开。

`[variables]` 可省略，直接填写完整路径即可。`${prompt_root}` 只用于配置文件中的路径，
仅展开一次，不读取环境变量；路径中需要字面量 `$` 时写 `$$`。

### TOML 中的 Windows 路径

Windows 配置推荐使用**单引号加反斜杠**，或**双引号加正斜杠**：

```toml
[variables]
prompt_root = 'C:\project\prompts'

[paths]
source_root = 'C:\project\source'
document_root = "C:/project/docs"
log_file = 'C:\project\logs\plsql.log'
```

上面仅展示需替换的路径部分，其余字段保留。Prompt 路径可写成
`'${prompt_root}\simple_user_prompts'`，单引号不影响变量引用。

| 写法 | 是否适用 |
| --- | --- |
| `'C:\project\source'` | 推荐 |
| `"C:/project/source"` | 推荐 |
| `"C:\\project\\source"` | 支持，但需双写反斜杠 |
| `"C:\project\source"` | 不要使用，可能报错或被错误解释 |

TOML 写法不随 CMD 或 PowerShell 改变。网络共享可写成 `'\\server\share\source'`，
运行账号需有访问权限。

## 运行前注意

- 所有业务路径必须是本机绝对路径；`--config` 指定的文件位置可以相对。
- 源码和四个 Prompt 路径必须已存在；文档输出目录和日志文件可以尚不存在。
- Windows 可用 `C:\...` 或 `C:/...`，不能用 `C:source` 或 `\source`。Linux 使用 `/...`；
  WSL 中访问 Windows 文件通常使用 `/mnt/c/...`，不要直接使用 `C:\...`。
- 参数名必须写完整，不支持缩写；字段不能为空，不在配置文件中放密钥。
- 不要同时运行多个命令写入同一文档输出目录。重试时保留 `document_root/.workbench`，
  修改输入或配置后，需确认原有进度仍适用。

## 结果与失败处理

| 退出码 | 含义与处理 |
| --- | --- |
| `0` | 生成和发布成功 |
| `1` | 生成阶段报告失败，未发布；查看日志，修正后重试 |
| `2` | 参数或配置错误；按终端提示修正后重试 |

其他异常会显示错误信息并以非零状态退出。失败时保留已保存的进度；
若错误发生在发布过程中，请检查输出文件，不保证自动回滚。
