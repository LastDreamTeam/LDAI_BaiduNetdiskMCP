# 百度网盘 MCP

> **自用项目** · 本仓库主要供维护者自用。

通过 bdpan 和可选的百度网盘开放平台授权管理用户的网盘文件。 本仓库是独立维护的第三方 stdio MCP 连接器。

## 安装运行环境

使用 Python 3.11 或更高版本。在仓库根目录执行 `python -m venv .venv`。Windows 用 `.venv\Scripts\python.exe -m pip install -r requirements.txt` 安装依赖，macOS/Linux 用 `.venv/bin/python -m pip install -r requirements.txt`。

## 连接自己的账户

1. 按官方说明安装 bdpan，并在运行 MCP 的操作系统账户下使用官方登录脚本完成浏览器授权。用 `bdpan whoami` 确认账户。可用 `BDPAN_BIN` 指定可执行文件绝对路径。[官方安装与登录](https://github.com/baidu-netdisk/bdpan-storage/tree/main/skills/baidu-drive)
2. 网盘读写使用 CLI 获准的应用目录，支持容量、目录、搜索、上传、下载、复制、移动、重命名和删除。交换目录默认为用户主目录下的 `BaiduMCP-Exchange`。下载核对结果包含大小和 SHA-256，同名目标会停止。
3. 已有开放平台应用的用户，可把自行取得的 OAuth access token 保存在本机私有文件，生成配置时追加 `--env BAIDU_ACCESS_TOKEN_FILE=/path/to/token-file`。此时可用 `baidu_authorized_list`、`baidu_authorized_search` 读取该应用授权的目录，用 `baidu_authorized_download` 按文件 ID 下载到本机交换目录。企业空间的可见范围取决于获批产品和账户授权。此令牌由调用者续期。[百度网盘开放平台](https://yun.baidu.com/open/platform)

## 接入 Cursor、DSH 或 Codex

在仓库目录执行对应命令。生成器使用 Python 3，按当前机器的路径输出可合并的客户端配置。

```sh
python client_config.py cursor
python client_config.py dsh
python client_config.py codex
```

| 客户端 | 配置位置 |
| --- | --- |
| Cursor | 项目 `.cursor/mcp.json` 或用户级 `~/.cursor/mcp.json` 的 `mcpServers` |
| DSH | 当前 profile 的插件列表，启用一条 `@deepseek-ai/dsh-mcp-client` 记录 |
| Codex | `~/.codex/config.toml` 的 `mcp_servers` 条目 |

可重复使用 `--env NAME=VALUE` 传递这些设置：`BDPAN_BIN`、`BDPAN_EXCHANGE_DIR`、`BAIDU_ACCESS_TOKEN_FILE`。涉及密钥的设置只填写私有文件的路径。在客户端刷新工具后先调用 `baidu_status`。各端使用同一个服务入口，克隆到另一台机器时重新生成路径。仓库另附 Codex 插件清单、图标和技能，可用于打包本地插件。若插件被复制到客户端缓存，可通过进程环境 `LDAI_MCP_PYTHON` 指定已安装依赖的 Python 绝对路径。 [Cursor MCP 文档](https://cursor.com/docs/mcp) · [DSH MCP 客户端](https://github.com/deepseek-ai/deepseek-harness/blob/main/packages/mcp/mcp-client/README.zh.md)

## 实现参考

GitHub 数据核对日期为 2026-11-11。

采用百度官方技能的 CLI 登录与应用目录约定，参照官方 MCP 增加独立 OAuth 目录查询。DSH 百度网盘项目作为客户端接入对照。

- [baidu-netdisk/bdpan-storage](https://github.com/baidu-netdisk/bdpan-storage) · 336 stars，默认分支 98 次提交
- [baidu-netdisk/mcp](https://github.com/baidu-netdisk/mcp) · 164 stars，默认分支 7 次提交
- [justinzyc271828-sys/dsh-baidu-netdisk](https://github.com/justinzyc271828-sys/dsh-baidu-netdisk) · 2 stars，默认分支 1 次提交

## 许可

本项目以 **GNU Affero General Public License v3.0 only（AGPL-3.0-only）** 发布。分发受许可覆盖的修改版或通过网络向用户提供修改版时，应按许可条款向相应用户提供对应源码。仅在本机自行使用无需公开发布。第三方服务及依赖遵循各自条款。[许可全文](LICENSE)
