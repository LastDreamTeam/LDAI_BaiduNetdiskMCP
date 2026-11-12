# 百度网盘的插件页账号连接

这是独立的 HTTPS MCP 服务。现有 stdio 插件和已有的网盘授权仍可使用。用户从插件详情页的「应用」区域点击「连接」，在浏览器中选择百度账号并授权。账号连接提供本人资料、目录、文件列表、云端搜索和分享能力查询。取得对应权限后可在选定目录内创建文件夹、生成带提取码的分享链接。原有的本机工具继续提供上传、批量传输和搜索。

**两条不同的回调：**

- 百度开放平台需要登记 **你的 HTTPS origin + `/pan/callback`**。百度 OAuth 的 `client_id` 是 AppKey；数字 AppID 是百度应用编号。AppKey 和 SecretKey 只放部署端，不填写到插件包。
- OpenAI 为 MCP 连接显示的回调是 **客户端返回地址**；将其精确填入 `OPENAI_REDIRECT_URI`。MCP URL 为 **你的 HTTPS origin + `/pan/mcp`**。已有百度令牌不要放进此 URL，也不要用百度 AppID 代替 OpenAI App ID。

独立 Python 3.11+ 虚拟环境中执行 `python -m pip install -r native_account/requirements.txt`。设置 `MCP_PUBLIC_ORIGIN`、`OPENAI_REDIRECT_URI`、`BAIDU_APP_KEY_FILE`、`BAIDU_APP_SECRET_FILE`、`BAIDU_ACCOUNT_ROOTS_FILE`；可设 `MCP_LISTEN_PORT`（默认 8201）。用不接触现有登录记录的独立服务用户运行 `python native_account/native_pan_server.py`。私有文件只允许服务用户读取；让服务用户拥有独立、持久化的 home，并保护其 FastMCP 授权存储。服务只监听回环地址。

账户策略文件使用 JSON。设置 `{"follow_directory_grants": true}` 后，新连接从百度的快速授权页取得本次选择的目录和实际权限，服务按每条连接分别执行。未返回有效目录时，这条新连接无法访问文件。可添加按百度用户 UK 命名的键限制目录上限，也可用 `directory_share_limits` 针对指定用户进一步限定分享目录。例如 `{"follow_directory_grants":true,"100001":["/Example"],"directory_share_limits":{"100001":["/Example/Published"]}}`。示例编号应替换为部署者自己的账号编号。

旧连接继续使用已经批准的读取目录，由用户 UK 对应的列表或 `default_roots` 提供。需要写入或分享时，分别用 `legacy_write_roots` 和 `share_roots` 按用户 UK 指定目录。读取整盘的旧授权不会自动获得整盘写入或分享。没有开启 `follow_directory_grants` 时，新连接选出的目录还受原有部署策略限制。

在浏览器打开的百度授权页勾选所需权限，再选择一个或多个目录并确认。同一次授权选中的目录共用这次勾选的权限。需要更换目录时点账号右侧的「重新连接」，按百度页面提示重新选择。授权完成后可用 `baidu_share_capabilities` 核对当前连接的读取、写入和分享范围。[百度网盘《快速授权》](https://pan.baidu.com/union/doc/使用入门/接入授权/快速授权/)

部署时仅反代 `/pan/mcp`、`/pan/authorize`、`/pan/register`、`/pan/token`、`/pan/consent`、`/pan/callback`、`/pan/revoke` 及 `/.well-known/oauth-protected-resource/pan/mcp`、`/.well-known/oauth-authorization-server/pan`。代理不得记录请求参数或 Authorization 头。示例只将带本服务事务前缀的回调送入服务，其他回调继续由原站点处理。具体片段见 `nginx.locations.example`。后端失败时回滚这几条路由即可，其余业务无需重启。

**验收：** `python native_account/native_pan_test.py` 检查独立账号、目录边界与未授权调用；`python native_account/pan_flow_test.py` 用两个模拟百度身份走完动态客户端注册、浏览器同意、百度回调、PKCE 换令牌和 MCP 调用，并检查同一账号的两个连接可以持有不同目录和权限，以及刷新后权限收紧。上游百度在测试中模拟。线上最终验收需连接真实百度账号、读取指定目录、再增加第二个账号并分别测试重连与断开。App 绑定或平台多账号 UI 没有完成前，不能把隔离测试称为已上线。

[OpenAI 原生多账号说明](https://developers.openai.com/plugins/build/auth#support-multiple-accounts) · [百度官方 MCP](https://github.com/baidu-netdisk/mcp)

## 为自己的插件登记连接

维护者在 ChatGPT 插件页选择「添加自定义 MCP 服务器」，填写自己的 HTTPS MCP 地址并选择 OAuth 动态客户端注册。平台生成账号连接后，从该连接的插件清单 `.app.json` 取得实际 App ID。网页回调使用平台给出的地址。Codex 桌面独立登录时还可登记本机随机端口，授权返回地址限定为本次客户端登记的回环地址。

运行 `python native_account/package_remote_oauth.py --help` 查看私有包生成参数。现有技能包装插件可用该生成器绑定 App ID。要在侧栏只呈现一项，应直接用 `package_account_app.py` 将技能打包到已登记的 App 插件，并使用平台签发的 .codex-plugin/plugin.json 保留该 App 和账号连接。插件用户进入原详情页的「应用」区域即可连接。生成器保留已有本机服务器和技能。私有连接地址、账号 App ID 及生成包应留在部署端。

账号操作可按以下顺序验收。

1. 从原插件点击「连接」，在浏览器完成百度授权，读取账号资料和一个已授权目录。
2. 从插件的账号设置连接第二个账号，分别检查其资料和目录，再测试重连与断开。
3. 目录上传和下载应按照所用工具的授权来源单独验证。浏览器账号可在目录策略允许的范围内搜索文件或创建分享链接。

### 云端搜索与文件分享

在插件管理页连接百度账号后，`baidu_search` 可以在允许读取的目录下按文件名搜索。`baidu_share_capabilities` 会向百度官方托管的 MCP 查询是否提供分享工具，并返回本连接允许分享的目录。`baidu_create_share` 接受一个或多个文件 ID、1／7／30 天有效期及可选的四位小写字母或数字提取码。省略提取码时服务随机生成，默认有效期七天。百度服务返回成功链接后才会返回给客户端。超时后先检查网盘原有分享记录。

创建分享前会核对文件 ID 所属目录、当前连接的分享范围及百度返回的操作结果。选择只读时，插件不能写入或创建公开链接。对于可写连接，实际分享还取决于用户在百度勾选的权限及应用获准的能力。分享使用百度提供的 `file_sharelink_set`，新建目录使用开放平台的文件创建接口。以上操作均可从云端连接调用。[百度官方 MCP](https://github.com/baidu-netdisk/mcp)
