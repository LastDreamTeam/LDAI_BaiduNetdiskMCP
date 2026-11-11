# SPDX-License-Identifier: AGPL-3.0-only
"""Expose isolated stdio MCP workers for the accounts configured on this machine."""
import asyncio
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent
PRODUCT = json.loads((ROOT / "connector.json").read_text(encoding="utf-8"))
NAME = PRODUCT["id"]
PROFILE_NAME = re.compile(r"[a-z][a-z0-9_-]{0,15}\Z")
LIMIT = 16 * 1024 * 1024

def profiles():
    override = os.environ.get("LDAI_ACCOUNTS_FILE")
    path = Path(override).expanduser() if override else Path.home() / ".config" / "ldai-mcp" / NAME / "accounts.json"
    if not path.is_file() and not override:
        return {"default": {}}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if data.get("connector") != NAME:
        raise ValueError("Account file is for a different connector.")
    entries = data["accounts"]
    if not isinstance(entries, dict) or not entries:
        raise ValueError("Define at least one named account.")
    for name, entry in entries.items():
        if not PROFILE_NAME.fullmatch(name) or not isinstance(entry, dict):
            raise ValueError("Invalid account alias or configuration.")
        env = entry.get("env", {})
        if not isinstance(env,dict) or not all(isinstance(k,str) and isinstance(v,str) for k,v in env.items()):
            raise ValueError("Account environment must contain strings.")
        if "command" in entry and (not isinstance(entry["command"],str) or not entry["command"]):
            raise ValueError("Invalid account worker.")
        if "args" in entry and ("command" not in entry or not isinstance(entry["args"],list) or
                               not all(isinstance(v,str) for v in entry["args"])):
            raise ValueError("Invalid account worker arguments.")
    seen = set()
    for alias, entry in entries.items():
        env = dict(entry.get("env",{}))
        if "accountHome" in entry:
            home = Path(entry["accountHome"]).expanduser()
            if not home.is_absolute():
                raise ValueError("accountHome must be an absolute private directory.")
            env.update(HOME=str(home),USERPROFILE=str(home),APPDATA=str(home/"AppData"/"Roaming"),
                       LOCALAPPDATA=str(home/"AppData"/"Local"),XDG_CONFIG_HOME=str(home/".config"),
                       XDG_DATA_HOME=str(home/".local"/"share"),XDG_CACHE_HOME=str(home/".cache"))
            if NAME=="baidu-netdisk":
                env.setdefault("BDPAN_CONFIG_DIR",str(home/".config"/"bdpan"))
                env.setdefault("BDPAN_EXCHANGE_DIR",str(home/"exchange"))
        if len(entries)>1 and "command" not in entry:
            required = PRODUCT["requiredMulti"]
            if NAME=="netease-music" and not entry.get("accountHome"):
                raise ValueError("Set a distinct accountHome for each music CLI account.")
            if NAME=="mailu" and env.get("MAILU_IMAP_USER"):
                required=["MAILU_IMAP_HOST","MAILU_IMAP_USER","MAILU_IMAP_PASSWORD_FILE"]
            for key in required:
                if not env.get(key):
                    raise ValueError(f"Account {alias} needs its own {key}.")
            identity=tuple(env[k] for k in required)
            if identity in seen:
                raise ValueError("Multiple named accounts use the same profile.")
            seen.add(identity)
        entry["env"] = env
    return entries

class Worker:
    def __init__(self, alias, config):
        self.alias = alias
        self.config = config
        self.lock = asyncio.Lock()
        self.proc = None
        self.seq = 0

    async def close(self):
        proc, self.proc = self.proc, None
        if proc and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()

    async def packet(self, method, params):
        self.seq += 1
        ident = self.seq
        msg = {"jsonrpc":"2.0","id":ident,"method":method,"params":params}
        self.proc.stdin.write((json.dumps(msg)+"\n").encode())
        await self.proc.stdin.drain()
        while raw := await self.proc.stdout.readline():
            event = json.loads(raw)
            if event.get("id") == ident and "method" not in event:
                if "error" in event:
                    raise ValueError("Worker rejected the MCP request.")
                return event["result"]
        raise ValueError("Worker closed the connection.")

    async def request(self, method, params):
        async with self.lock:
            try:
                async with asyncio.timeout(970 if method == "tools/call" else 40):
                    if self.proc is None or self.proc.returncode is not None:
                        env = dict(os.environ)
                        if len(WORKERS) > 1:
                            for k in PRODUCT["providerEnvironment"]:
                                env.pop(k,None)
                        env.update(self.config.get("env",{}))
                        if "command" in self.config:
                            argv = [self.config["command"],*self.config.get("args",[])]
                        elif PRODUCT["runtime"] == "node":
                            argv = ["node",str(ROOT/"server.mjs"),"--stdio"]
                        else:
                            argv = [sys.executable,str(ROOT/"launch.py")]
                        self.proc = await asyncio.create_subprocess_exec(*argv, cwd=ROOT, env=env,
                            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.DEVNULL,limit=LIMIT)
                        await self.packet("initialize",{"protocolVersion":"2025-06-18","capabilities":{},
                                                       "clientInfo":{"name":"account-router","version":"0.2.0"}})
                        self.proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
                        await self.proc.stdin.drain()
                    return await self.packet(method,params)
            except BaseException:
                await self.close()
                raise

WORKERS = {}
ROUTES = {}
ERRORS = {}
discovery_lock = asyncio.Lock()

def exposed(alias, tool):
    name = (alias+"__"+tool) if len(WORKERS)>1 else tool
    if len(name)>64:
        name=name[:51]+"_"+hashlib.sha256(name.encode()).hexdigest()[:12]
    return name

async def discover():
    async with discovery_lock:
        async def tools_for(alias,w):
            found=[]
            cursor=None
            try:
                while True:
                    result=await w.request("tools/list",{"cursor":cursor} if cursor else {})
                    for tool in result["tools"]:
                        name=tool["name"]
                        if not name.startswith(tuple(PRODUCT["toolPrefixes"])):
                            continue
                        public=exposed(alias,name)
                        found.append((public,name,{**tool,"name":public,"description":
                            (f"[{alias}] " if len(WORKERS)>1 else "")+tool.get("description","")}))
                    cursor=result.get("nextCursor")
                    if not cursor:
                        break
            except Exception:
                ERRORS[alias]="Worker unavailable. Check this account's runtime and configuration."
                return []
            ERRORS.pop(alias,None)
            return [(public,(w,name),schema) for public,name,schema in found]
        groups=await asyncio.gather(*(tools_for(k,w) for k,w in WORKERS.items()))
        names={}
        schemas=[]
        for group in groups:
            for public, route, schema in group:
                if public in names or public=="ldai_accounts":
                    raise ValueError("Conflicting tool names.")
                names[public]=route
                schemas.append(schema)
        ROUTES.clear()
        ROUTES.update(names)
        return [{"name":"ldai_accounts","description":"List configured account aliases and discovery status.",
                 "inputSchema":{"type":"object","properties":{},"additionalProperties":False}},*schemas]

def payload(data, failed=False):
    return {"content":[{"type":"text","text":json.dumps(data,ensure_ascii=False)}],"isError":failed}

async def handle(r):
    method=r.get("method")
    if method=="initialize":
        supported=("2024-11-05","2025-03-26","2025-06-18","2025-11-25")
        version=r.get("params",{}).get("protocolVersion")
        return {"protocolVersion":version if version in supported else "2025-06-18","capabilities":{"tools":{}},
                "serverInfo":{"name":NAME,"version":PRODUCT["version"]}}
    if method=="ping":
        return {}
    if method=="tools/list":
        return {"tools":await discover()}
    if method=="tools/call":
        p=r["params"]
        if p["name"]=="ldai_accounts":
            return payload({"accounts":list(WORKERS),"discoveryErrors":dict(ERRORS)})
        if not ROUTES:
            await discover()
        route=ROUTES.get(p["name"])
        if not route:
            return payload("Unknown tool or account. Refresh the tool list.",True)
        w,name=route
        try:
            return await w.request("tools/call",{**p,"name":name})
        except Exception:
            return payload("Account worker failed. Verify the operation on the provider before retrying.",True)
    raise LookupError

async def main():
    WORKERS.update({alias:Worker(alias,cfg) for alias,cfg in profiles().items()})
    pending={}
    async def respond(r):
        resp={"jsonrpc":"2.0","id":r["id"]}
        try:
            resp["result"]=await handle(r)
        except LookupError:
            resp["error"]={"code":-32601,"message":"Method not found"}
        except Exception:
            resp["error"]={"code":-32603,"message":"Connector request failed"}
        sys.stdout.buffer.write((json.dumps(resp,ensure_ascii=False)+"\n").encode())
        sys.stdout.buffer.flush()
    try:
        while line:=await asyncio.to_thread(sys.stdin.buffer.readline,LIMIT):
            try:
                r=json.loads(line)
            except ValueError:
                continue
            if not isinstance(r,dict):
                continue
            if r.get("method")=="notifications/cancelled":
                t=pending.get(r.get("params",{}).get("requestId"))
                if t:t.cancel()
            elif "id" in r:
                t=asyncio.create_task(respond(r))
                pending[r["id"]]=t
                t.add_done_callback(lambda done,i=r["id"]:pending.pop(i,None))
        if pending:
            await asyncio.gather(*pending.values(),return_exceptions=True)
    finally:
        await asyncio.gather(*(w.close() for w in WORKERS.values()))
if __name__=="__main__":
    asyncio.run(main())
