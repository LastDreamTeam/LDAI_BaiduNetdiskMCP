# SPDX-License-Identifier: AGPL-3.0-only
"""Use the current user's official bdpan session through MCP stdio."""
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
from typing import Annotated, Literal
from uuid import uuid4

from mcp.server.fastmcp import FastMCP
from pydantic import Field,BaseModel
from cli_support import run_json
from open_platform import query, download_file

mcp = FastMCP("baidu-netdisk")
SESSION = uuid4().hex
Text = Annotated[str, Field(min_length=1, max_length=180)]

def cli(args, intent, timeout=90):
    exe = os.environ.get("BDPAN_BIN") or shutil.which("bdpan")
    if not exe or (os.name == "nt" and Path(exe).suffix.lower() != ".exe"):
        raise ValueError("Install the official bdpan executable or set BDPAN_BIN.")
    return run_json([exe, *args, "--json", "--no-check-update", "--agentname", "ldai-mcp",
                     "--session-input", intent, "--session-id", SESSION], timeout)

def remote(value, allow_root=False):
    if value == "." and allow_root:
        base=os.getenv("BDPAN_APP_ROOT","").strip("/")
        if base:
            if "\\" in base or ":" in base or any(x in ("",".","..") or x.startswith("-") for x in base.split("/")):raise ValueError("Invalid BDPAN_APP_ROOT.")
            return base
        return value
    if not value or value.startswith(("/", "-")) or "\\" in value or ":" in value or any(
        p in ("", ".", "..") for p in value.split("/")
    ) or any(ord(c) < 32 for c in value):
        raise ValueError("Expected a relative bdpan application path.")
    base=os.getenv("BDPAN_APP_ROOT","").strip("/")
    if base and ("\\" in base or ":" in base or any(x in ("",".","..") or x.startswith("-") for x in base.split("/"))):
        raise ValueError("Invalid BDPAN_APP_ROOT.")
    return base+"/"+value if base else value

def local(name):
    if not name or Path(name).name != name or any(c in name for c in '/\\:') or name in (".","..") or Path(name).is_reserved() or name.endswith((" ", ".")):
        raise ValueError("Use a single filename in BDPAN_EXCHANGE_DIR.")
    root = Path(os.environ.get("BDPAN_EXCHANGE_DIR") or Path.home() / "BaiduMCP-Exchange").expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root / name
    if target.is_symlink() or target.resolve().parent != root:
        raise ValueError("File outside exchange directory.")
    return target

@mcp.tool()
def baidu_status():
    """Read the current bdpan user's login status."""
    return cli(["whoami"], "检查网盘登录")

@mcp.tool()
def baidu_quota():
    """Read available storage and usage."""
    return cli(["quota"], "查看网盘容量")

@mcp.tool()
def baidu_list(path: str = "."):
    """List the contents of the application directory or a relative folder."""
    folder = remote(path, True)
    return cli(["ls"] if folder == "." else ["ls", folder], "查看网盘目录")

@mcp.tool()
def baidu_search(keyword: Text, page: Annotated[int, Field(ge=1)] = 1, limit: Annotated[int, Field(ge=1, le=50)] = 20):
    """Search filenames in the application's authorized scope."""
    if os.getenv("BDPAN_APP_ROOT","").strip("/"):
        raise ValueError("For a scoped subdirectory use baidu_list or baidu_authorized_search with the configured OAuth directory.")
    if keyword.startswith("-"):
        raise ValueError("Expected a search term.")
    return cli(["search", keyword, "--page", str(page), "--page-size", str(limit)], "搜索网盘文件")

@mcp.tool()
def baidu_mkdir(path: str):
    """Create a directory within the current account's application space."""
    return cli(["mkdir", remote(path)], "新建网盘目录")

@mcp.tool()
def baidu_manage(action: Literal["copy","move","rename","remove"], path: str, destination: str = ""):
    """Copy/move to a directory, rename to a basename, or remove an explicitly selected item."""
    source = remote(path)
    if action == "remove":
        return cli(["rm",source,"--force"], "删除指定网盘项目")
    target = remote(destination, allow_root=action != "rename") if action!="rename" else destination
    if action == "rename" and (not target or target in (".","..") or target.startswith("-") or any(c in target for c in "/\\:") or any(ord(c)<32 for c in target)):
        raise ValueError("Rename takes a basename.")
    return cli([{"copy":"cp","move":"mv","rename":"rename"}[action],source,target],"管理指定网盘项目")

@mcp.tool()
def baidu_upload_file(local_name: str, destination: str):
    """Upload a regular file from the exchange directory, using the CLI's default conflict behavior."""
    source = local(local_name)
    if not source.is_file():
        raise ValueError("File missing from exchange directory.")
    return cli(["upload",str(source),remote(destination)],"上传选定文件",900)

@mcp.tool()
def baidu_download_file(path: str, local_name: str):
    """Download one file into the exchange directory without replacing an existing file."""
    target = local(local_name)
    if target.exists():
        raise ValueError("Destination exists.")
    with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
        stage = Path(tmp) / "download"
        cli(["download",remote(path),str(stage)],"下载选定文件",900)
        if not stage.is_file() or stage.is_symlink():
            raise ValueError("Expected one downloaded file.")
        with stage.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        os.link(stage, target)
    return {"filename":target.name,"bytes":target.stat().st_size,"sha256":digest}

@mcp.tool()
def baidu_upload_text(text: str, destination: str):
    """Create a UTF-8 text file in the application's cloud directory."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "document.txt"
        path.write_text(text, encoding="utf-8")
        return cli(["upload",str(path),remote(destination)],"上传文本",900)

@mcp.tool()
def baidu_authorized_list(directory: str = "/", start: Annotated[int, Field(ge=0)] = 0,
                          limit: Annotated[int, Field(ge=1,le=500)] = 100):
    """List a directory permitted by your separately authorized Open Platform application."""
    return query("list",dir=os.getenv("BAIDU_OAUTH_ROOT","/") if directory=="/" else directory,start=start,limit=limit)

@mcp.tool()
def baidu_authorized_search(keyword: Text, directory: str = "/", page: Annotated[int,Field(ge=1)] = 1,
                            limit: Annotated[int,Field(ge=1,le=100)] = 30):
    """Search filenames in the OAuth application's authorized directory tree."""
    return query("search",dir=os.getenv("BAIDU_OAUTH_ROOT","/") if directory=="/" else directory,key=keyword,page=page,num=limit,recursion=1)

@mcp.tool()
def baidu_authorized_download(file_id: Annotated[int,Field(gt=0)], local_name: str,
                              max_bytes: Annotated[int,Field(gt=0,le=536870912)] = 67108864,
                              rate_kib_s:Annotated[int,Field(ge=0,le=1048576)]=0):
    """Download one OAuth-authorized file by fs_id into the exchange directory."""
    return download_file(file_id,local(local_name),max_bytes,rate_kib_s)


from baidu_transfer import upload,scoped
from open_platform import metadata,token_value
import json

class UploadItem(BaseModel):
    local_name: str
    destination: str

@mcp.tool()
def baidu_batch_upload(files:Annotated[list[UploadItem],Field(min_length=1,max_length=100)],
                       mode:Literal["application","oauth"]="application",
                       rate_kib_s:Annotated[int,Field(ge=0,le=1048576)]=0):
    """Upload sequentially. OAuth paths are absolute and support a KiB/s cap. Stop on first failure without replay."""
    if mode=="application" and rate_kib_s:raise ValueError("Choose OAuth mode for paced transfers.")
    selected=[(local(i.local_name),scoped(i.destination) if mode=="oauth" else remote(i.destination)) for i in files]
    if len(set(dst for _,dst in selected))!=len(files) or not all(s.is_file() for s,_ in selected):
        raise ValueError("Missing input file or duplicate destination.")
    completed=[]
    for i,(s,dst) in enumerate(selected):
        try:
            result=upload(s,dst,rate_kib_s) if mode=="oauth" else baidu_upload_file(files[i].local_name,files[i].destination)
        except Exception:
            return {"completed":completed,"unconfirmed":dst,"remaining":[p for _,p in selected[i+1:]],"inspect_before_retry":True}
        completed.append({"destination":dst,"result":result})
    return {"completed":completed}

@mcp.tool()
def baidu_authorized_upload(local_name:str,destination:str,rate_kib_s:Annotated[int,Field(ge=0,le=1048576)]=0):
    """Upload a file to an OAuth-authorized directory with an optional KiB/s cap."""
    return upload(local(local_name),destination,rate_kib_s)

@mcp.tool()
def baidu_file_info(file_id:Annotated[int,Field(gt=0)]):
    """Read file size, path, type and modification time by fs_id."""
    files=metadata("multimedia",token_value(),{"method":"filemetas","fsids":json.dumps([file_id])})
    for f in files:
        if str(f.get("fs_id"))==str(file_id):
            scoped(f["path"]);return {k:f.get(k) for k in ("fs_id","path","size","isdir","server_mtime","md5")}
    raise ValueError("File unavailable to this account.")

@mcp.tool()
def baidu_directory_size(directory:str,max_pages:Annotated[int,Field(ge=1,le=100)]=20):
    """Sum descendant file bytes across bounded directory pages; complete=false identifies a partial total."""
    from collections import deque
    queue=deque([(scoped(directory),0)]);seen={directory};size=count=pages=0
    while queue and pages<max_pages:
        path,start=queue.popleft();rows=query("list",dir=path,start=start,limit=500);pages+=1
        for item in rows:
            key=scoped(item["path"])
            if key in seen:continue
            seen.add(key)
            if item["isdir"]:queue.append((key,0))
            else:size+=int(item["size"]);count+=1
        if len(rows)==500:queue.appendleft((path,start+500))
    return {"directory":directory,"bytes":size,"files":count,"pages":pages,"complete":not queue}

if __name__ == "__main__":
    mcp.run(transport="stdio")
