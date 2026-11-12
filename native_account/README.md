# 百度网盘的插件页账号连接

这是独立的 HTTPS MCP 服务。现有 stdio 插件和已有的网盘授权仍可使用。用户从插件详情页的「应用」区域点击「连接」，在浏览器中选择百度账号并授权。账号连接提供本人资料、可访问目录和文件列表。原有的本机工具继续提供上传、批量传输和搜索。

**两条不同的回调：**

- 百度开放平台需要登记 **你的 HTTPS origin + `/pan/callback`**。百度 OAuth 的 `client_id` 是 AppKey；数字 AppID 是百度应用编号。AppKey 和 SecretKey 只放部署端，不填写到插件包。
- OpenAI 为 MCP 连接显示的回调是 **客户端返回地址**；将其精确填入 `OPENAI_REDIRECT_URI`。MCP URL 为 **你的 HTTPS origin + `/pan/mcp`**。已有百度令牌不要放进此 URL，也不要用百度 AppID 代替 OpenAI App ID。

独立 Python 3.11+ 虚拟环境中执行 `python -m pip install -r native_account/requirements.txt`。设置 `MCP_PUBLIC_ORIGIN`、`OPENAI_REDIRECT_URI`、`BAIDU_APP_KEY_FILE`、`BAIDU_APP_SECRET_FILE`、`BAIDU_ACCOUNT_ROOTS_FILE`；可设 `MCP_LISTEN_PORT`（默认 8201）。用不接触现有登录记录的独立服务用户运行 `python native_account/native_pan_server.py`。私有文件只允许服务用户读取；让服务用户拥有独立、持久化的 home，并保护其 FastMCP 授权存储。服务只监听回环地址。

账户目录文件为 JSON，例如 `{"100001":["/Example"]}`（样例编号）。键是这次登录的百度用户 UK，**不是**开发应用 AppID。可选键 `default_roots` 给未单独配置的新授权账号指定可读目录；未设置时这些用户无法读目录，显式配置 `/` 才允许整个网盘。例中的用户只能查询 `/Example` 及子目录。修改允许目录后下次读取生效。百度授权页可能显示网盘读写权限，但此桥不提供写工具。`baidu_allowed_directories` 可列出当前账号的可用目录。

部署时仅反代 `/pan/mcp`、`/pan/authorize`、`/pan/register`、`/pan/token`、`/pan/consent`、`/pan/callback`、`/pan/revoke` 及 `/.well-known/oauth-protected-resource/pan/mcp`、`/.well-known/oauth-authorization-server/pan`。代理不得记录请求参数或 Authorization 头。示例只将带本服务事务前缀的回调送入服务，其他回调继续由原站点处理。具体片段见 `nginx.locations.example`。后端失败时回滚这几条路由即可，其余业务无需重启。

**验收：** `python native_account/native_pan_test.py` 检查独立账号、目录边界与未授权调用；`python native_account/pan_flow_test.py` 用两个模拟百度身份走完动态客户端注册、浏览器同意、百度回调、PKCE 换令牌和 MCP 调用。上游百度在测试中模拟。线上最终验收需连接真实百度账号、读取指定目录、再增加第二个账号并分别测试重连与断开。App 绑定或平台多账号 UI 没有完成前，不能把隔离测试称为已上线。

[OpenAI 原生多账号说明](https://developers.openai.com/plugins/build/auth#support-multiple-accounts) · [百度官方 MCP](https://github.com/baidu-netdisk/mcp)

## 为自己的插件登记连接

维护者在 ChatGPT 插件页选择「添加自定义 MCP 服务器」，填写自己的 HTTPS MCP 地址并选择 OAuth 动态客户端注册。平台生成账号连接后，从该连接的插件清单 `.app.json` 取得实际 App ID。网页回调使用平台给出的地址。Codex 桌面独立登录时还可登记本机随机端口，授权返回地址限定为本次客户端登记的回环地址。

运行 `python native_account/package_remote_oauth.py --help` 查看私有包生成参数。向 `--app-id` 传入已核实的 OpenAI App ID，并将生成的包更新到原插件。插件用户进入原详情页的「应用」区域即可连接。生成器保留已有本机服务器和技能。私有连接地址、账号 App ID 及生成包应留在部署端。

账号操作可按以下顺序验收。

1. 从原插件点击「连接」，在浏览器完成百度授权，读取账号资料和一个已授权目录。
2. 从插件的账号设置连接第二个账号，分别检查其资料和目录，再测试重连与断开。
3. 目录上传和下载应按照所用工具的授权来源单独验证。浏览器账号当前提供只读工具。

### 云端搜索与文件分享

在插件管理页连接百度账号后，`baidu_search` 可以在允许读取的目录下按文件名搜索。`baidu_share_capabilities` 会向百度官方托管的 MCP 查询是否提供分享工具，并返回本连接允许分享的目录。`baidu_create_share` 接受一个或多个文件 ID、1／7／30 天有效期及可选的四位小写字母或数字提取码。省略提取码时服务随机生成，默认有效期七天。百度服务返回成功链接后才会返回给客户端。超时后先检查网盘原有分享记录。

分享需要在部署端原有目录策略中增加按百度用户 ID 配置的 `share_roots` 对象，例如 `"share_roots": {"100001": ["/demo"]}`。分享目录和读取目录同时校验。旧的只读策略继续生效。上游使用 [百度官方 MCP](https://github.com/baidu-netdisk/mcp) 提供的 `file_sharelink_set`；应用的授权资格由百度判定。以上操作全部在服务器端通过当前选定的百度账号完成。
