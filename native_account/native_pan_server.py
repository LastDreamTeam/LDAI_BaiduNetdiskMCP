# SPDX-License-Identifier: AGPL-3.0-only
"""Baidu account connection for OpenAI clients. Use one upstream grant per request."""
import json
import logging
import os
from pathlib import Path
from typing import Annotated, Any
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


async def provider_request(token: str, endpoint: str, params: dict) -> dict:
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        try:
            response = await client.get(UPSTREAM + endpoint,
                                        params={**params, "access_token": token},
                                        headers={"User-Agent": "pan.baidu.com"})
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


class BaiduOAuthProxy(OAuthProxy):
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
        return super()._build_upstream_authorize_url("ldpan1." + txn_id, transaction)

    async def _handle_idp_callback(self, request):
        states = request.query_params.getlist("state")
        if len(states) != 1 or not states[0].startswith("ldpan1."):
            return PlainTextResponse("Unknown authorization transaction", status_code=400)
        params = [(k, v.removeprefix("ldpan1.") if k == "state" else v)
                  for k, v in request.query_params.multi_items()]
        scope = dict(request.scope, query_string=urlencode(params).encode("ascii"))
        return await super()._handle_idp_callback(Request(scope, request.receive))

    def _prepare_scopes_for_token_exchange(self, scopes):
        return []

    def _prepare_scopes_for_upstream_refresh(self, scopes):
        return []

    def _translate_scopes_from_idp(self, scopes):
        granted = set(" ".join(scopes).replace(",", " ").split())
        return [READ_SCOPE] if READ_SCOPE in granted else []


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


def read_roots(policy, uid):
    if policy is None:
        raise ValueError("No authorized directories configured.")
    value = json.loads(policy.read_text(encoding="utf-8-sig"))
    roots = value.get(uid, value.get("default_roots")) if isinstance(value, dict) else None
    if not isinstance(roots, list) or not roots or not all(isinstance(x, str) for x in roots):
        raise ValueError("No authorized directories configured for this account.")
    return [checked_path(x) for x in roots]


def register_read_tools(server, grant=current_grant, lookup=None, roots=None):
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
        return roots(bound.claims["profile"]["id"]) if roots is not None else []

    @server.tool(name="baidu_list", annotations={"readOnlyHint": True, "destructiveHint": False})
    async def directory(path: str = "/", start: Annotated[int,Field(ge=0)] = 0,
                        limit: Annotated[int,Field(ge=1, le=100)] = 30) -> dict[str, Any]:
        """Read a page of items from the current connection's Baidu account."""
        bound = grant()
        permitted = roots(bound.claims["profile"]["id"]) if roots is not None else []
        path = checked_path(path)
        if not any(within(path,r) for r in permitted):
            raise ValueError("Directory is outside this account's allowed roots.")
        result = await (lookup or provider_request)(bound.token, "file", {"method": "list", "dir": path, "start": start, "limit": limit})
        items = result.get("list")
        if not isinstance(items, list):
            raise ValueError("Unexpected Baidu directory response")
        if any(not isinstance(row,dict) or not isinstance(row.get("path"),str)
               or not within(checked_path(row["path"]), path) for row in items):
            raise ValueError("Unexpected file path in Baidu response.")
        return {"items": [{k: item[k] for k in ("fs_id","path","size","isdir","server_mtime") if k in item}
                          for item in items], "start": start}


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
    register_read_tools(server, roots=lambda uid: read_roots(policy, uid))
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
