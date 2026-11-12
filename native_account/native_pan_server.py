# SPDX-License-Identifier: AGPL-3.0-only
"""Baidu account connection for OpenAI clients. Use one upstream grant per request."""
import json
from contextlib import asynccontextmanager
from contextvars import ContextVar
import logging
import re
import secrets
import os
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import PlainTextResponse

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.routing import Mount
from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken, OAuthProxy, TokenVerifier
from fastmcp.server.dependencies import get_access_token
from mcp.server.auth.provider import RegistrationError
from pydantic import Field

# Baidu specifies access_token in the URL. Exclude URL-bearing HTTP client logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
UPSTREAM = "https://pan.baidu.com/rest/2.0/xpan/"
READ_SCOPE = "netdisk"
PROFILE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["id"],
                  "properties": {"id": {"type": "string", "minLength": 1},
                                 "name": {"type": "string"}}}


async def provider_request(token: str, endpoint: str, params: dict, *, form: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        try:
            response = await client.request("POST" if form is not None else "GET", UPSTREAM + endpoint,
                                            params={**params, "access_token": token}, data=form,
                                            headers={"User-Agent":"pan.baidu.com"})
            if response.status_code != 200:
                raise ValueError("Baidu did not accept the request")
            result = response.json()
        except (httpx.HTTPError, ValueError):
            raise ValueError("Baidu request failed") from None
    if not isinstance(result, dict) or result.get("errno") != 0:
        raise ValueError("Baidu did not authorize this operation")
    return result


def desktop_callback(uri: str) -> bool:
    """Codex listens on a random unprivileged local port for the OAuth redirect."""
    try:
        parsed = urlsplit(uri)
        port = parsed.port
    except ValueError:
        return False
    return (port is not None and 1024 <= port <= 65535
            and uri == f"http://127.0.0.1:{port}/callback")


DIRECTORY_FLOW = ContextVar("baidu_directory_flow", default=False)
DIRECTORY_SCOPES = "nd_open_read,nd_open_write"
DIRECTORY_AUTHORIZE = "https://pan.baidu.com/union/oauth/authorize"
DIRECTORY_TOKEN = "https://pan.baidu.com/xpan/oauth/get"


def provider_scopes(value):
    if isinstance(value, str):
        return sorted(set(re.split(r"[,\s]+", value)) - {""})
    return []


def provider_paths(value):
    """An empty, missing or malformed selection must never become the full drive."""
    if not isinstance(value, list) or not all(isinstance(p, str) for p in value):
        return []
    try:
        return list(dict.fromkeys(checked_path(p) for p in value))
    except ValueError:
        return []


class DirectoryOAuthClient:
    def __init__(self, key, secret):
        self.key = key
        self.secret = secret

    async def _request(self, method, params):
        try:
            # Baidu documents GET for this opaque token exchange. Log neither request URLs nor responses.
            async with httpx.AsyncClient(timeout=18, follow_redirects=False) as client:
                response = await client.get(DIRECTORY_TOKEN, params={
                    "method": method, "client_id": self.key, "client_secret": self.secret, **params})
                response.raise_for_status()
                envelope = response.json()
            if not isinstance(envelope, dict) or envelope.get("errno") != 0:
                raise ValueError()
            data = envelope["data"]
            if not isinstance(data, dict) or not all(isinstance(data.get(k), str) and data[k]
                    for k in ("access_token","refresh_token","scope")) or int(data["expires_in"]) <= 0:
                raise ValueError()
            # An explicitly empty selection and malformed selections are denied on the file tools.
            paths = provider_paths(data.get("authorized_paths"))
            return {k: data[k] for k in ("access_token","refresh_token","scope","expires_in")} | {
                "token_type":"Bearer", "authorized_paths":paths, "_baidu_directory_flow":True}
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise ValueError("Baidu did not complete directory authorization.") from None

    async def fetch_token(self, *, code, redirect_uri, **kwargs):
        return await self._request("token", {"code":code, "redirect_uri":redirect_uri})

    async def refresh_token(self, *, refresh_token, **kwargs):
        return await self._request("refresh_token", {"refresh_token":refresh_token})


def intersect_roots(first, second):
    result = []
    for a in first:
        for b in second:
            child = a if within(a, b) else b if within(b, a) else None
            if child is not None and child not in result:
                result.append(child)
    return result


def account_permissions(bound, read_policy, share_policy, *, directory_policy=None, legacy_write_policy=None):
    uid = bound.claims["profile"]["id"]
    details = bound.claims.get("baidu_grant", {})
    directory = details.get("directory_flow", False)
    scopes = set(details.get("scopes", []))
    has_paths = "paths" in details
    if directory and directory_policy is not None:
        read_limit, share_limit = directory_policy(uid)
    else:
        read_limit = read_policy(uid)
        share_limit = share_policy(uid)
    roots = intersect_roots(details.get("paths", []), read_limit) if has_paths or directory else read_limit
    if directory and not ("nd_open_read" in scopes or "netdisk" in scopes):
        roots = []
    if directory:
        writable = roots if "nd_open_write" in scopes else []
        shareable = writable if share_limit is None else intersect_roots(writable, share_limit)
    else:
        # A legacy netdisk grant does not attest to broad writable directory rights.
        writable = (intersect_roots(roots, legacy_write_policy(uid))
                    if legacy_write_policy and "netdisk" in scopes else [])
        shareable = intersect_roots(roots, share_limit) if (not details or "netdisk" in scopes or "nd_open_write" in scopes) else []
    return {"read_roots":roots, "write_roots":writable, "share_roots":shareable,
            "authorization_source":"baidu_directory_oauth" if directory else "existing_connection",
            "provider_returned_directories":has_paths,
            "provider_scopes":sorted(s for s in scopes if s in ("basic","netdisk") or re.fullmatch(r"nd_open_[a-z_]{1,48}",s))}


class BaiduOAuthProxy(OAuthProxy):
    async def load_access_token(self, token):
        verified = await super().load_access_token(token)
        if verified is None:
            return None
        row = await self._grant_record(token, "access")
        if row is None or row.access_token != verified.token:
            return None
        raw = row.raw_token_data
        # Keep effective permissions private to this connection and current after refresh.
        permissions = {
            "directory_flow": raw.get("_baidu_directory_flow") is True,
            "scopes": provider_scopes(raw.get("scope", "")),
        }
        if "authorized_paths" in raw:
            permissions["paths"] = provider_paths(raw["authorized_paths"])
        return verified.model_copy(update={"claims": {**(verified.claims or {}), "baidu_grant": permissions}})

    async def _grant_record(self, token, use):
        try:
            payload = self.jwt_issuer.verify_token(token, expected_token_use=use)
        except Exception:
            return None
        mapping = await self._jti_mapping_store.get(key=payload["jti"])
        return await self._upstream_token_store.get(key=mapping.upstream_token_id) if mapping else None

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        row = await self._grant_record(refresh_token.token, "refresh")
        mode = DIRECTORY_FLOW.set(bool(row and row.raw_token_data.get("_baidu_directory_flow")))
        try:
            return await super().exchange_refresh_token(client, refresh_token, scopes)
        finally:
            DIRECTORY_FLOW.reset(mode)

    async def _try_transparent_refresh(self, upstream_token_set):
        mode = DIRECTORY_FLOW.set(bool(upstream_token_set.raw_token_data.get("_baidu_directory_flow")))
        try:
            return await super()._try_transparent_refresh(upstream_token_set)
        finally:
            DIRECTORY_FLOW.reset(mode)

    @asynccontextmanager
    async def _upstream_oauth_client(self):
        if DIRECTORY_FLOW.get():
            yield DirectoryOAuthClient(self._upstream_client_id, self._upstream_client_secret.get_secret_value())
        else:
            async with super()._upstream_oauth_client() as client:
                yield client


    async def register_client(self, client_info):
        uris = [str(uri) for uri in (client_info.redirect_uris or [])]
        if not uris or any(uri != self.openai_callback and not desktop_callback(uri) for uri in uris):
            raise RegistrationError("invalid_redirect_uri", "Unsupported client callback.")
        return await super().register_client(client_info)

    async def get_client(self, client_id):
        client = await super().get_client(client_id)
        if client is not None:
            # Reuse only the callback addresses registered by this specific client.
            client.allowed_redirect_uri_patterns = [
                str(uri) for uri in (client.redirect_uris or [])
                if str(uri) == self.openai_callback or desktop_callback(str(uri))
            ]
        return client

    # Only our transactions use this state prefix. Existing users of the same
    # Baidu callback must continue through the original web application.
    def _build_upstream_authorize_url(self, txn_id, transaction):
        return DIRECTORY_AUTHORIZE + "?" + urlencode({
            "client_id": self._upstream_client_id,
            "redirect_uri": f"{str(self.base_url).rstrip('/')}{self._redirect_path}",
            "scope": DIRECTORY_SCOPES, "state": "ldpan1.directory_v2_" + txn_id})

    async def _handle_idp_callback(self, request):
        states = request.query_params.getlist("state")
        if len(states) != 1 or not states[0].startswith("ldpan1."):
            return PlainTextResponse("Unknown authorization transaction", status_code=400)
        directory = states[0].startswith("ldpan1.directory_v2_")
        prefix = "ldpan1.directory_v2_" if directory else "ldpan1."
        params = [(k, v.removeprefix(prefix) if k == "state" else v)
                  for k, v in request.query_params.multi_items()]
        if directory and request.query_params.get("errno") not in (None, "0"):
            params = [(k,v) for k,v in params if k not in ("code","error","error_description")]
            params.append(("error","access_denied"))
        scope = dict(request.scope, query_string=urlencode(params).encode("ascii"))
        mode = DIRECTORY_FLOW.set(directory)
        try:
            return await super()._handle_idp_callback(Request(scope, request.receive))
        finally:
            DIRECTORY_FLOW.reset(mode)

    def _prepare_scopes_for_token_exchange(self, scopes):
        return []

    def _prepare_scopes_for_upstream_refresh(self, scopes):
        return []

    def _translate_scopes_from_idp(self, scopes):
        granted = set(" ".join(scopes).replace(",", " ").split())
        return [READ_SCOPE] if granted.intersection({READ_SCOPE,"nd_open_read"}) else []


class BaiduTokenVerifier(TokenVerifier):
    def __init__(self, app_key: str, lookup=provider_request):
        super().__init__(required_scopes=[READ_SCOPE])
        self.app_key = app_key
        self.lookup = lookup

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            record = await self.lookup(token, "nas", {"method": "uinfo"})
        except ValueError:
            return None
        uk = record.get("uk")
        if type(uk) not in (str, int) or not str(uk).isascii() or not str(uk).isdigit() or int(uk) <= 0:
            return None
        profile = {"id": str(uk)}
        display = record.get("netdisk_name") or record.get("baidu_name")
        if isinstance(display, str) and display.strip():
            profile["name"] = display.strip()
        return AccessToken(token=token, client_id=self.app_key, scopes=[READ_SCOPE],
                           claims={"profile": profile})


def current_grant() -> AccessToken:
    grant = get_access_token()
    if not grant or not grant.claims.get("profile") or READ_SCOPE not in grant.scopes:
        raise ValueError("Connect the Baidu account to continue.")
    return grant


def checked_path(value: str) -> str:
    if (not value.startswith("/") or "//" in value or "\\" in value or
            any(x in (".","..") for x in value.split("/")) or any(ord(c) < 32 for c in value)):
        raise ValueError("An absolute, canonical Baidu directory is required.")
    return value.rstrip("/") or "/"


def within(value, root):
    return value == root or root == "/" or value.startswith(root + "/")


def register_read_tools(server, grant=current_grant, lookup=None, roots=None, permissions=None):
    def readable(bound):
        if permissions is not None:
            return permissions(bound)["read_roots"]
        return account_permissions(bound, roots or (lambda uid: []), lambda uid: [])["read_roots"]

    @server.tool(name="baidu_profile", output_schema=PROFILE_SCHEMA,
                 annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
                 meta={"openai/profile": True})
    def profile() -> dict[str, str]:
        """Return the Baidu account associated with the current authorized connection."""
        bound = grant()
        # Return only this authenticated user's own identity. Data access is checked separately.
        return dict(bound.claims["profile"])

    @server.tool(name="baidu_allowed_directories", annotations={"readOnlyHint": True})
    def directories() -> list[str]:
        """List the directories allowed for this authenticated account."""
        bound = grant()
        return readable(bound)

    @server.tool(name="baidu_list", annotations={"readOnlyHint": True, "destructiveHint": False})
    async def directory(path: str = "/", start: Annotated[int,Field(ge=0)] = 0,
                        limit: Annotated[int,Field(ge=1, le=100)] = 30) -> dict[str, Any]:
        """Read a page of items from the current connection's Baidu account."""
        bound = grant()
        permitted = readable(bound)
        path = checked_path(path)
        if not any(within(path,r) for r in permitted):
            raise ValueError("Directory is outside this account's allowed roots.")
        result = await (lookup or provider_request)(bound.token, "file", {"method": "list", "dir": path, "start": start, "limit": limit})
        items = result.get("list")
        return {"items": allowed_rows(items, path), "start": start}


def allowed_rows(items, path):
    if not isinstance(items, list) or any(not isinstance(item, dict) or
            not isinstance(item.get("path"), str) or not within(checked_path(item["path"]), path)
            for item in items):
        raise ValueError("Unexpected file path in Baidu response.")
    return [{k: item[k] for k in ("fs_id","path","size","isdir","server_mtime") if k in item} for item in items]


class ShareServiceUnavailable(ValueError):
    """Baidu did not confirm the share request or tool listing."""


async def official_share_call(token, args=None):
    """Use Baidu's hosted MCP and its documented share tool. Never log a credential-bearing URL."""
    import asyncio
    from mcp import ClientSession
    from mcp.client.sse import sse_client
    logging.getLogger("mcp.client.sse").setLevel(logging.CRITICAL)
    url = "https://mcp-pan.baidu.com/sse?" + urlencode({"access_token":token})
    try:
        async with asyncio.timeout(30):
            async with sse_client(url, timeout=8, sse_read_timeout=15) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    if args is None:
                        tools = await session.list_tools()
                        return any(t.name == "file_sharelink_set" for t in tools.tools)
                    reply = await session.call_tool("file_sharelink_set", args)
                    if reply.isError:
                        raise ValueError()
                    value = reply.structuredContent
                    if value is None:
                        texts = [p.text for p in reply.content if p.type == "text"]
                        value = json.loads(texts[0]) if len(texts) == 1 else None
                    if isinstance(value, dict) and isinstance(value.get("data"), dict):
                        value = value["data"]
                    if not isinstance(value, dict):
                        raise ValueError()
                    link = value.get("link") or value.get("short_url")
                    target = urlsplit(link or "")
                    if target.scheme != "https" or target.netloc != "pan.baidu.com" or not target.path.startswith("/s/"):
                        raise ValueError()
                    return {"link":link,"pwd":args["pwd"],"period":args["period"]}
    except Exception:
        raise ShareServiceUnavailable("Baidu MCP could not confirm this request. For creation, check the account's existing shares before retrying.") from None


def register_cloud_tools(server, roots, shares, grant=current_grant, lookup=None, share_gateway=None, permissions=None):
    if permissions is None:
        def permissions(bound):
            return account_permissions(bound, roots, shares)

    @server.tool(name="baidu_search", annotations={"readOnlyHint":True,"destructiveHint":False})
    async def search(keyword: Annotated[str,Field(min_length=1,max_length=255)], path: str="/",
                     page: Annotated[int,Field(ge=1)]=1, limit: Annotated[int,Field(ge=1,le=100)]=30) -> dict:
        """Search filenames in an authorized directory of the selected account, including subdirectories."""
        bound=grant()
        base=checked_path(path)
        if not keyword.strip() or not any(within(base,r) for r in permissions(bound)["read_roots"]):
            raise ValueError("Choose a keyword and an authorized search directory.")
        result=await (lookup or provider_request)(bound.token,"file",{"method":"search","dir":base,"key":keyword,"recursion":1,"page":page,"num":limit})
        return {"items":allowed_rows(result.get("list"),base),"page":page,"limit":limit,"has_more":bool(result.get("has_more",False))}

    @server.tool(name="baidu_create_directory", annotations={"readOnlyHint":False,"destructiveHint":False,"idempotentHint":False})
    async def create_directory(path: str) -> dict:
        """Create a new folder underneath a directory selected with write permission. Existing items are not replaced."""
        bound=grant()
        path=checked_path(path)
        parent=path.rsplit("/",1)[0] or "/"
        allowed=permissions(bound)["write_roots"]
        if path=="/" or not any(within(parent,root) for root in allowed):
            raise ValueError("Choose a parent directory with write permission.")
        result=await (lookup or provider_request)(bound.token,"file",{"method":"create"},
                    form={"path":path,"size":"0","isdir":"1","rtype":"0"})
        if result.get("path") != path or result.get("isdir") not in (1,True):
            raise ValueError("Baidu did not confirm this directory. Check its parent before retrying.")
        return {k:result[k] for k in ("path","fs_id","isdir") if k in result}

    @server.tool(name="baidu_share_capabilities", annotations={"readOnlyHint":True,"destructiveHint":False})
    async def capabilities() -> dict:
        """Check whether Baidu advertises link creation to this connection and show its permitted sharing directories."""
        bound=grant()
        access = permissions(bound)
        try:
            available = await (share_gateway or official_share_call)(bound.token)
        except ShareServiceUnavailable:
            # A catalog timeout does not revoke the user's directory grant.
            return {**access, "provider_tool_available": None, "provider_status": "unconfirmed"}
        return {**access, "provider_tool_available": available, "provider_status": "checked"}

    @server.tool(name="baidu_create_share", annotations={"readOnlyHint":False,"destructiveHint":False,"idempotentHint":False,"openWorldHint":True})
    async def create_share(file_ids: Annotated[list[str],Field(min_length=1,max_length=100)],
                           days: Literal[1,7,30]=7, extraction_code: Annotated[str,Field(pattern="^[a-z0-9]{4}$")]|None=None) -> dict:
        """Create a time-limited sharing link for explicitly selected files. Anyone with the link and code can access them."""
        bound=grant()
        access=permissions(bound)
        reading,sharing=access["read_roots"],access["share_roots"]
        if not sharing:raise ValueError("Sharing has not been enabled for this account.")
        if any(not re.fullmatch(r"[1-9][0-9]{0,19}",v) for v in file_ids) or len(set(file_ids))!=len(file_ids):
            raise ValueError("Supply unique decimal file identifiers.")
        result=await (lookup or provider_request)(bound.token,"multimedia",{"method":"filemetas","fsids":json.dumps([int(v) for v in file_ids])})
        items=result.get("list")
        if not isinstance(items,list) or not all(isinstance(x,dict) for x in items) or {str(x.get("fs_id")) for x in items}!=set(file_ids):
            raise ValueError("Baidu could not verify every file in this account.")
        for item in items:
            path=checked_path(item.get("path",""))
            if not any(within(path,r) for r in reading) or not any(within(path,r) for r in sharing):
                raise ValueError("The selected file is outside the allowed sharing directories.")
        pwd=extraction_code or "".join(secrets.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(4))
        return await (share_gateway or official_share_call)(bound.token,{"fsid_list":json.dumps(file_ids),"period":days,"pwd":pwd})



def build_server(public_origin: str, redirect_uri: str, app_key: str, app_secret: str, policy: Path, *, storage=None):
    origin = urlsplit(public_origin)
    redirect = urlsplit(redirect_uri)
    if (origin.scheme != "https" or not origin.hostname or origin.username or origin.password
            or origin.query or origin.fragment or origin.path not in ("","/")):
        raise ValueError("Configure a public HTTPS origin without a path.")
    if (redirect.scheme != "https" or redirect.netloc != "chatgpt.com" or redirect.query or redirect.fragment
            or "*" in redirect_uri or not (redirect.path == "/connector_platform_oauth_redirect" or
               (redirect.path.startswith("/connector/oauth/") and len(redirect.path.split("/")) == 4 and redirect.path.rsplit("/",1)[1]))):
        raise ValueError("Use the exact callback from this OpenAI MCP connection.")
    if not app_key.strip() or not app_secret.strip():
        raise ValueError("Baidu application credentials are required.")
    auth = BaiduOAuthProxy(base_url=public_origin.rstrip("/") + "/pan", redirect_path="/callback",
                      upstream_authorization_endpoint="https://openapi.baidu.com/oauth/2.0/authorize",
                      upstream_token_endpoint="https://openapi.baidu.com/oauth/2.0/token",
                      upstream_client_id=app_key, upstream_client_secret=app_secret,
                      allowed_client_redirect_uris=[redirect_uri, "http://127.0.0.1:*/callback"], valid_scopes=[READ_SCOPE],
                      client_storage=storage, token_verifier=BaiduTokenVerifier(app_key), forward_pkce=False, forward_resource=False,
                      token_endpoint_auth_method="client_secret_post", enable_cimd=False,
                      extra_authorize_params={"scope":"basic,netdisk"},
                      require_authorization_consent=True)
    auth.openai_callback = redirect_uri
    server = FastMCP("Baidu Netdisk", auth=auth)
    def permissions(bound):
        # One policy snapshot per operation, including both read and share ceilings.
        data=json.loads(policy.read_text(encoding="utf-8-sig"))
        def allowed_read(uid):
            return provider_paths(data.get(uid, data.get("default_roots", [])))
        def allowed_share(uid):
            return provider_paths(data.get("share_roots", {}).get(uid, []))
        def directory_limits(uid):
            if data.get("follow_directory_grants") is not True:
                return allowed_read(uid), allowed_share(uid)
            ceiling=provider_paths(data.get(uid, ["/"]))
            entries=data.get("directory_share_limits", {})
            return ceiling, provider_paths(entries[uid]) if uid in entries else None
        return account_permissions(bound, allowed_read, allowed_share, directory_policy=directory_limits,
                                   legacy_write_policy=lambda uid: provider_paths(data.get("legacy_write_roots", {}).get(uid, [])))
    register_read_tools(server, permissions=permissions)
    register_cloud_tools(server, None, None, permissions=permissions)
    return server


class OAuthIssuerResponse:
    """RFC 9207 issuer identification for the registered client's authorization responses."""
    def __init__(self, app, *, issuer, callback):
        self.app = app
        self.issuer = issuer
        self.callback = urlsplit(callback)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        discovery = scope["path"] in {
            "/.well-known/oauth-authorization-server/pan", "/.well-known/openid-configuration/pan"
        }
        start = None
        parts = []

        async def respond(message):
            nonlocal start
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                target = urlsplit(headers.get("location", ""))
                callback_uri = urlunsplit(target._replace(query="", fragment=""))
                trusted = target[:3] == self.callback[:3] or desktop_callback(callback_uri)
                if message["status"] in (302,303,307,308) and trusted:
                    fields = parse_qsl(target.query, keep_blank_values=True)
                    if any(key in ("code","error") for key, _ in fields):
                        fields = [(key, val) for key, val in fields if key != "iss"]
                        fields.append(("iss", self.issuer))
                        headers["location"] = urlunsplit(target._replace(query=urlencode(fields)))
                if (discovery and scope["method"] == "GET" and message["status"] == 200
                        and headers.get("content-type", "").startswith("application/json")):
                    start = message
                    return
            elif start is not None and message["type"] == "http.response.body":
                parts.append(message.get("body", b""))
                if message.get("more_body"):
                    return
                data = json.loads(b"".join(parts))
                data["authorization_response_iss_parameter_supported"] = True
                body = json.dumps(data).encode()
                MutableHeaders(scope=start)["content-length"] = str(len(body))
                await send(start)
                return await send({"type": "http.response.body", "body": body})
            await send(message)
        await self.app(scope, receive, respond)


def http_app(server):
    child = server.http_app(path="/mcp", stateless_http=True, json_response=True)
    routes = [route for route in server.auth.get_well_known_routes("/mcp")
              if route.path.endswith(("/pan", "/pan/mcp"))]
    routes.append(Mount("/pan", app=child))
    app = Starlette(routes=routes, lifespan=child.router.lifespan_context)
    app.add_middleware(OAuthIssuerResponse, issuer=str(server.auth.issuer_url), callback=server.auth.openai_callback)
    return app


if __name__ == "__main__":
    key = Path(os.environ["BAIDU_APP_KEY_FILE"]).read_text(encoding="utf-8-sig").strip()
    secret = Path(os.environ["BAIDU_APP_SECRET_FILE"]).read_text(encoding="utf-8-sig").strip()
    mcp = build_server(os.environ["MCP_PUBLIC_ORIGIN"], os.environ["OPENAI_REDIRECT_URI"], key, secret,
                       Path(os.environ["BAIDU_ACCOUNT_ROOTS_FILE"]))
    uvicorn.run(http_app(mcp), host="127.0.0.1", port=int(os.getenv("MCP_LISTEN_PORT", "8201")),
                access_log=False)
