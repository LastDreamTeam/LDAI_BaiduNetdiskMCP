# SPDX-License-Identifier: AGPL-3.0-only
"""Read metadata through the account holder's separately authorized Baidu application."""
import hashlib
import json
import tempfile
import os
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.error import URLError

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None

def token_value():
    filename = os.getenv("BAIDU_ACCESS_TOKEN_FILE")
    if not filename:
        raise ValueError("Configure BAIDU_ACCESS_TOKEN_FILE with your application's OAuth access token.")
    try:
        token = Path(filename).expanduser().read_text(encoding="utf-8-sig").strip()
    except OSError:
        raise ValueError("Cannot open BAIDU_ACCESS_TOKEN_FILE.") from None
    if token.startswith("{"):
        token=json.loads(token).get("access_token","")
    if not token:
        raise ValueError("Empty OAuth token file.")
    return token

def metadata(endpoint, token, params):
    url = "https://pan.baidu.com/rest/2.0/xpan/" + endpoint + "?" + urlencode({**params,"access_token":token})
    try:
        with build_opener(NoRedirect()).open(Request(url,headers={"User-Agent":"pan.baidu.com"}), timeout=30) as response:
            data = response.read(4 * 1024 * 1024 + 1)
    except (URLError, OSError, TimeoutError):
        raise ValueError("Could not retrieve Baidu Open Platform metadata.") from None
    if len(data) > 4 * 1024 * 1024:
        raise ValueError("Use a smaller page.")
    result = json.loads(data)
    if result.get("errno") != 0 or not isinstance(result.get("list"), list):
        raise ValueError("Baidu rejected the directory query. Check OAuth scope, token expiry and path.")
    return result["list"]

def query(method, **params):
    if not params["dir"].startswith("/") or ".." in params["dir"].split("/"):
        raise ValueError("Supply an absolute directory within the authorized account.")
    from baidu_transfer import scoped
    scoped(params["dir"])
    entries = metadata("file", token_value(), {"method":method, **params})
    for item in entries:
        if item.get("path"):scoped(item["path"])
        elif os.getenv("BAIDU_OAUTH_ROOT","/")!="/":raise ValueError("Missing scoped path.")
    fields = ("fs_id","path","server_filename","isdir","size","server_mtime")
    return [{k:item[k] for k in fields if k in item} for item in entries]

def trusted_download(url):
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme != "https" or parts.username or parts.password or parts.port not in (443,None) or not (
        host == "pan.baidu.com" or host.endswith(".baidupcs.com") or host == "baidupcs.com"
    ):
        raise ValueError("Baidu provided an unsupported download endpoint.")
    return parts

class DownloadRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        trusted_download(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def download_file(file_id, target, max_bytes,rate_kib_s=0):
    from baidu_transfer import scoped,Pacer
    pacer=Pacer(rate_kib_s)
    if target.exists():
        raise ValueError("Destination already exists.")
    token = token_value()
    entries = metadata("multimedia", token, {"method":"filemetas","fsids":json.dumps([file_id]),"dlink":1})
    info = next((i for i in entries if str(i.get("fs_id")) == str(file_id)), None)
    if not info or info.get("isdir") or not info.get("dlink"):
        raise ValueError("No downloadable file in this authorization scope.")
    if info.get("path"):
        scoped(info["path"])
    elif os.environ.get("BAIDU_OAUTH_ROOT","/")!="/":
        raise ValueError("No path available to verify the scoped directory.")
    expected = int(info["size"])
    if expected < 0 or expected > max_bytes:
        raise ValueError("File exceeds the requested size limit.")
    parts = trusted_download(info["dlink"])
    params = [(k,v) for k,v in parse_qsl(parts.query,keep_blank_values=True) if k != "access_token"]
    url = urlunsplit(parts._replace(query=urlencode(params+[("access_token",token)]),fragment=""))
    req = Request(url,headers={"User-Agent":"pan.baidu.com"})
    with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
        stage = Path(tmp)/"download"
        received = 0
        digest = hashlib.sha256()
        try:
            with build_opener(DownloadRedirect()).open(req,timeout=60) as response, stage.open("xb") as stream:
                while chunk := response.read(min(65536,max(1024,pacer.rate//4)) if pacer.rate else 1024*1024):
                    pacer.wait(len(chunk))
                    received += len(chunk)
                    if received > expected:
                        raise ValueError("Received more data than the declared file size.")
                    digest.update(chunk)
                    stream.write(chunk)
        except (URLError,TimeoutError,OSError):
            raise ValueError("Baidu file transfer failed. Check the destination and account authorization.") from None
        if received != expected:
            raise ValueError("Incomplete transfer.")
        os.link(stage, target)
    return {"filename":target.name,"bytes":received,"sha256":digest.hexdigest()}
