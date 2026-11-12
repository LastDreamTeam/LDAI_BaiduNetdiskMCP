# 百度网盘：Codex 原生账号连接候选版

这是独立的 HTTPS MCP 服务。现有 stdio 插件和已有的网盘授权仍可使用。目标是从插件设置连接或重连百度账号，并让每次目录查询只使用所选连接的授权。此候选版仅包含本人资料和目录查询；上传、批量传输、搜索等仍由原安装提供，尚未迁移到原生连接。

**两条不同的回调：**

- 百度开放平台需要登记 **你的 HTTPS origin + `/pan/callback`**。百度 OAuth 的 `client_id` 是 AppKey；数字 AppID 是百度应用编号。AppKey 和 SecretKey 只放部署端，不填写到插件包。
- OpenAI 为 MCP 连接显示的回调是 **客户端返回地址**；将其精确填入 `OPENAI_REDIRECT_URI`。MCP URL 为 **你的 HTTPS origin + `/pan/mcp`**。已有百度令牌不要放进此 URL，也不要用百度 AppID 代替 OpenAI App ID。

独立 Python 3.11+ 虚拟环境中执行 `python -m pip install -r native_account/requirements.txt`。设置 `MCP_PUBLIC_ORIGIN`、`OPENAI_REDIRECT_URI`、`BAIDU_APP_KEY_FILE`、`BAIDU_APP_SECRET_FILE`、`BAIDU_ACCOUNT_ROOTS_FILE`；可设 `MCP_LISTEN_PORT`（默认 8201）。用不接触现有登录记录的独立服务用户运行 `python native_account/native_pan_server.py`。私有文件只允许服务用户读取；让服务用户拥有独立、持久化的 home，并保护其 FastMCP 授权存储。服务只监听回环地址。

账户目录文件为 JSON，例如 `{"100001":["/Example"]}`（样例编号）。键是这次登录的百度用户 UK，**不是**开发应用 AppID。可选键 `default_roots` 给未单独配置的新授权账号指定可读目录；未设置时这些用户无法读目录，显式配置 `/` 才允许整个网盘。例中的用户只能查询 `/Example` 及子目录。修改允许目录后下次读取生效。百度授权页可能显示网盘读写权限，但此桥不提供写工具。`baidu_allowed_directories` 可列出当前账号的可用目录。

部署时仅反代 `/pan/mcp`、`/pan/authorize`、`/pan/register`、`/pan/token`、`/pan/consent`、`/pan/callback`、`/pan/revoke` 及 `/.well-known/oauth-protected-resource/pan/mcp`、`/.well-known/oauth-authorization-server/pan`。代理不得记录请求参数或 Authorization 头。示例只将带本服务事务前缀的回调送入服务，其他回调继续由原站点处理。具体片段见 `nginx.locations.example`。后端失败时回滚这几条路由即可，其余业务无需重启。

**验收：** `python native_account/native_pan_test.py` 检查独立账号、目录边界与未授权调用；`python native_account/pan_flow_test.py` 用两个模拟百度身份走完动态客户端注册、浏览器同意、百度回调、PKCE 换令牌和 MCP 调用。上游百度在测试中模拟。线上最终验收需连接真实百度账号、读取指定目录、再增加第二个账号并分别测试重连与断开。App 绑定或平台多账号 UI 没有完成前，不能把隔离测试称为已上线。

[OpenAI 原生多账号说明](https://developers.openai.com/plugins/build/auth#support-multiple-accounts) · [百度官方 MCP](https://github.com/baidu-netdisk/mcp)

平台成功登记后，从 OpenAI 实际发现结果复制本服务的 App ID，再运行 `python native_account/prepare_app_binding.py <解包的当前私有插件> <已核实的AppID> <全新的私有目录>`，生成三份待审核的清单。此工具不上传、不替换现行插件，不会将旧 stdio 工具自动转成按账号路由。保留原 ZIP 作为回退；旧工具与原生工具容易混淆时，不应同时展示同名工具。先用平台测试连接完成双账号验收，并确定每个操作走选中账号，再对原插件 ID 发布。不要将生成的 App ID 或依赖清单提交到公共仓库。
