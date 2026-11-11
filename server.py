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
from pydantic import Field
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
        return value
    if not value or value.startswith(("/", "-")) or "\\" in value or ":" in value or any(
        p in ("", ".", "..") for p in value.split("/")
    ) or any(ord(c) < 32 for c in value):
        raise ValueError("Expected a relative bdpan application path.")
    return value

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
    target = remote(destination, allow_root=action != "rename")
    if action == "rename" and "/" in target:
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
    return query("list",dir=directory,start=start,limit=limit)

@mcp.tool()
def baidu_authorized_search(keyword: Text, directory: str = "/", page: Annotated[int,Field(ge=1)] = 1,
                            limit: Annotated[int,Field(ge=1,le=100)] = 30):
    """Search filenames in the OAuth application's authorized directory tree."""
    return query("search",dir=directory,key=keyword,page=page,num=limit,recursion=1)

@mcp.tool()
def baidu_authorized_download(file_id: Annotated[int,Field(gt=0)], local_name: str,
                              max_bytes: Annotated[int,Field(gt=0,le=536870912)] = 67108864):
    """Download one OAuth-authorized file by fs_id into the exchange directory."""
    return download_file(file_id,local(local_name),max_bytes)

if __name__ == "__main__":
    mcp.run(transport="stdio")
