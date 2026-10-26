# Access 文档生成使用指南

`access-docs` 生成界面和入口文档，两个阶段均成功后发布结果。
以下命令在已安装此工具及生成服务的应用项目中运行。

## 使用配置文件（推荐）

复制 [配置模板](configs/access-rms-prod.toml)，修改路径和项目标识：

```bash
uv run access-docs run --config configs/access-rms-prod.toml
```

查看全部参数：

```bash
uv run access-docs run --help
```

## 不使用配置文件

不传 `--config` 时，必须完整提供下面的业务参数，不会自动读取默认配置文件。
**不能将 `--config` 与业务参数混用，也不支持命令行覆盖配置文件。**

### Linux Bash

```bash
uv run access-docs run \
  --source-root /srv/project/source \
  --document-root /srv/project/docs \
  --log-file /srv/project/logs/form.log \
  --lookup-ref-csv-path /srv/project/lookup.csv \
  --table-file-folder-path /srv/project/schema/tables_views \
  --sys-prompt-directory /srv/project/prompts/access \
  --ui-user-prompt-directory /srv/project/prompts/access/ui_user_prompts \
  --vba-user-prompt-directory /srv/project/prompts/access/vba_user_prompts \
  --system-id rms --env-id prod
```

### Windows CMD

```bat
uv run access-docs run ^
  --source-root "C:\project\source" ^
  --document-root "C:\project\docs" ^
  --log-file "C:\project\logs\form.log" ^
  --lookup-ref-csv-path "C:\project\lookup.csv" ^
  --table-file-folder-path "C:\project\schema\tables_views" ^
  --sys-prompt-directory "C:\project\prompts\access" ^
  --ui-user-prompt-directory "C:\project\prompts\access\ui_user_prompts" ^
  --vba-user-prompt-directory "C:\project\prompts\access\vba_user_prompts" ^
  --system-id rms --env-id prod
```

Linux 用 `\` 续行，CMD 用 `^`，PowerShell 用反引号（`` ` ``）；续行符后不要加空格。
CMD 参数使用双引号，反斜杠无需双写，带引号的目录路径建议省略末尾反斜杠。
也可以将全部参数写在同一行，去掉续行符。

## 配置内容

以下为 Windows 配置，所有字段都需填写：

```toml
[variables]
prompt_root = 'C:\project\prompts\access'

[project]
system_id = "rms"
env_id = "prod"

[paths]
source_root = 'C:\project\source'
document_root = 'C:\project\docs'
log_file = 'C:\project\logs\form.log'
lookup_ref_csv_path = 'C:\project\lookup.csv'
table_file_folder_path = 'C:\project\schema\tables_views'

[prompts]
sys_prompt_directory = '${prompt_root}'
ui_user_prompt_directory = '${prompt_root}\ui_user_prompts'
vba_user_prompt_directory = '${prompt_root}\vba_user_prompts'
```

| 配置项 | 用途 |
| --- | --- |
| `system_id` / `env_id` | 系统和环境标识 |
| `source_root` | 待分析源码位置 |
| `document_root` | 文档输出位置 |
| `log_file` | 运行日志文件 |
| `lookup_ref_csv_path` | 界面关联参考 CSV |
| `table_file_folder_path` | 表结构资料位置 |
| `sys_prompt_directory` | 系统 Prompt 位置 |
| `ui_user_prompt_directory` | 界面分析 Prompt 位置 |
| `vba_user_prompt_directory` | VBA 入口分析 Prompt 位置 |

Linux 配置将路径改为 `/srv/project/...`，例如
`prompt_root = "/srv/project/prompts/access"` 和
`ui_user_prompt_directory = "${prompt_root}/ui_user_prompts"`。
Windows TOML 推荐单引号反斜杠或双引号正斜杠，不要直接写 `"C:\project\source"`。
更多路径写法和变量规则见 [PL/SQL 使用指南](README.md#toml-中的-windows-路径)。

## 运行前注意与结果

所有业务路径必须是本机绝对路径，`--config` 文件位置可以相对。源码、CSV、表结构资料和
三个 Prompt 路径必须已存在；文档输出目录和日志文件可以尚不存在。参数名必须写完整，
字段不能为空，不在配置文件中放密钥。

不要同时运行多个命令写入同一文档输出目录。失败时保留 `document_root/.workbench`，
查看日志并修正问题后重试；修改输入或配置后，需确认原有进度仍适用。

| 退出码 | 含义 |
| --- | --- |
| `0` | 分析和发布成功 |
| `1` | 分析阶段报告失败，未发布；查看日志 |
| `2` | 参数或配置错误；按终端提示修正 |

其他异常会显示错误信息并以非零状态退出。若错误发生在发布过程中，请检查输出文件，
不保证自动回滚。
