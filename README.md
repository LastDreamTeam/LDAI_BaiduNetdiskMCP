# Baidu Netdisk MCP

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

## 账号管理窗口

账号版从本插件详情页点「管理」→「已连接的账户」→「连接另一个账户」。已连接账号右侧菜单提供重命名、重新连接和断开连接。

在浏览器打开的百度授权页核对应用显示的操作权限，在「可访问目录」中选择目录并确认。同一次授权选中的目录共用该次授予的操作权限。需要更换目录时点账号右侧的「重新连接」，按百度页面提示重新选择。授权完成后可用 `baidu_share_capabilities` 核对当前连接的读取、写入和分享范围。[百度网盘《快速授权》](https://pan.baidu.com/union/doc/使用入门/接入授权/快速授权/)

使用独立 stdio 客户端时可运行 `python account_manager.py` 设置本机账号。站点与凭据保存在部署端；图形窗口需要 Python 的 Tk 组件。

bdpan 官方登录脚本完成应用空间授权。BDPAN_APP_ROOT 可限定应用内目录，BAIDU_OAUTH_ROOT 限定开放平台目录，实际权限由服务商授予。开放平台 Token 文件需要目标目录及上传权限。支持文件元数据、带分页预算的目录大小、批量上传和以 KiB/s 为单位的开放平台分块上传/下载限速。批量失败返回已完成、待核对和未尝试的项目。限速大文件可能超过客户端工具调用时限；请按文件大小调整时限或分批传输。

## 多账号和终端更新

MCP 入口为 `router.py`，需要 Python 3.11+。主机在本地保存账号配置，默认位置为 `~/.config/ldai-mcp/baidu-netdisk/accounts.json`，也可用 `LDAI_ACCOUNTS_FILE` 指定。复制 [两账号示例](examples/accounts.example.json)，将地址与路径换成自己的值。每个账号使用单独的 bdpan 配置目录。登录官方 CLI 时设置该账号的 BDPAN_CONFIG_DIR，accountHome 默认将它指向该目录下的 .config/bdpan，并单独保存交换文件。

`ldai_accounts` 仅列出别名和连接状态。配置两个账号后，工具以 `work__` 和 `personal__` 前缀区分，所选工具始终进入该账号的独立子进程。已有单账号配置继续使用原工具名。运行 `python profile_exec.py work -- <程序> <参数…>` 可在同一账号环境完成登录或初始化。完成配置后重新连接 MCP。需要自有适配器时，管理员可为某个账号填写可信 stdio 程序的 `command`、`args` 和 `env`；连接器仅发布当前产品前缀下的业务工具。

安装本仓库后，其他终端可运行 `git pull --ff-only`；Python 服务在其虚拟环境执行 `python -m pip install -r requirements.txt`，随后重新加载客户端。需要固定版本时可检出发布标签。

维护者在 main 提交改进、运行测试，并同步更新 connector.json、两个插件清单的版本，再执行 `python build_plugin.py --push`。此命令将 main 与版本标签一起推送到本仓库，再从已提交文件生成 ZIP。更新既有账号插件时传 `--account-name <既有包名>`，把生成的 ZIP 发布到同一插件。包内 release-source.json 记录精确提交，可与各终端核对。Git 推送与账号插件发布是两个独立步骤。

许可为 **AGPL-3.0-only**。[许可全文](LICENSE)

开放平台账号可在管理窗口填写自己的 Client ID、Client Secret 文件路径和 Token 文件路径，点击「重新连接」后在百度官方页面输入设备码。成功后原子替换本机授权，失败保留旧授权。应用空间和开放平台的授权目录可分别设置。[百度官方授权接口](https://github.com/baidu-netdisk/baidu-drive-sdk-go/tree/main/baidudriver/api)

[从插件页连接百度账号及部署自己的 OAuth 服务](native_account/README.md)
