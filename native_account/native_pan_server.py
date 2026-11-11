# SPDX-License-Identifier: AGPL-3.0-only
"""Baidu account connection for OpenAI clients. Use one upstream grant per request."""
import json
import logging
import os
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.routing import Mount
from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken, OAuthProxy, TokenVerifier
from fastmcp.server.dependencies import get_access_token
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


class BaiduOAuthProxy(OAuthProxy):
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
    roots = value.get(uid) if isinstance(value, dict) else None
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
                      allowed_client_redirect_uris=[redirect_uri], valid_scopes=[READ_SCOPE],
                      client_storage=storage, token_verifier=BaiduTokenVerifier(app_key), forward_pkce=False, forward_resource=False,
                      token_endpoint_auth_method="client_secret_post", enable_cimd=False,
                      extra_authorize_params={"scope":"basic,netdisk"},
                      require_authorization_consent=True)
    server = FastMCP("Baidu Netdisk", auth=auth)
    register_read_tools(server, roots=lambda uid: read_roots(policy, uid))
    return server


def http_app(server):
    child = server.http_app(path="/mcp", stateless_http=True, json_response=True)
    routes = [route for route in server.auth.get_well_known_routes("/mcp")
              if route.path.endswith(("/pan", "/pan/mcp"))]
    routes.append(Mount("/pan", app=child))
    return Starlette(routes=routes, lifespan=child.router.lifespan_context)


if __name__ == "__main__":
    key = Path(os.environ["BAIDU_APP_KEY_FILE"]).read_text(encoding="utf-8-sig").strip()
    secret = Path(os.environ["BAIDU_APP_SECRET_FILE"]).read_text(encoding="utf-8-sig").strip()
    mcp = build_server(os.environ["MCP_PUBLIC_ORIGIN"], os.environ["OPENAI_REDIRECT_URI"], key, secret,
                       Path(os.environ["BAIDU_ACCOUNT_ROOTS_FILE"]))
    uvicorn.run(http_app(mcp), host="127.0.0.1", port=int(os.getenv("MCP_LISTEN_PORT", "8201")),
                access_log=False)
