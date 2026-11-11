# SPDX-License-Identifier: AGPL-3.0-only
"""Interactive Baidu device authorization on the operator's desktop."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode,urlsplit
from urllib.request import build_opener
import webbrowser
from account_manager import locked
from open_platform import NoRedirect

def request(path,params):
    url="https://openapi.baidu.com/oauth/2.0/"+path+"?"+urlencode(params)
    try:
        with build_opener(NoRedirect()).open(url,timeout=20) as res:raw=res.read(65537)
    except HTTPError as e:
        with e:raw=e.read(65537)
    except (OSError,URLError):raise ValueError("百度授权服务连接失败。") from None
    if len(raw)>65536:raise ValueError("授权响应过大。")
    return json.loads(raw)

def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).digest() if path.exists() else None

def authorize(inform):
    key=os.environ.get("BAIDU_CLIENT_ID")
    secret=Path(os.environ["BAIDU_CLIENT_SECRET_FILE"]).expanduser().read_text(encoding="utf-8-sig").strip()
    path=Path(os.environ["BAIDU_ACCESS_TOKEN_FILE"]).expanduser()
    if not key or not secret or path.is_symlink():raise ValueError("请设置应用 Client ID、密钥文件和私有授权文件。")
    previous=fingerprint(path)
    session=request("device/code",{"response_type":"device_code","client_id":key,"scope":"basic,netdisk"})
    link=session.get("verification_url","")
    dest=urlsplit(link)
    if dest.scheme not in ("https","http") or dest.hostname!="openapi.baidu.com" or dest.username or dest.password:
        raise ValueError("百度未返回官方登录地址。")
    link=dest._replace(scheme="https").geturl()
    interval=max(1,int(session.get("interval",5)))
    end=time.monotonic()+min(1800,int(session["expires_in"]))
    inform(link,session["user_code"])
    while time.monotonic()+interval<=end:
        time.sleep(interval)
        grant=request("token",{"grant_type":"device_token","code":session["device_code"],"client_id":key,"client_secret":secret})
        if grant.get("error")=="authorization_pending":continue
        if grant.get("error")=="slow_down":interval+=5;continue
        if not grant.get("access_token"):raise ValueError("授权被拒绝或已过期，旧授权仍保留。")
        credentials={k:grant[k] for k in ("access_token","refresh_token","scope","expires_in") if k in grant}
        credentials["issued_at"]=int(time.time())
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with locked(path.with_name(path.name+".lock")):
            if fingerprint(path)!=previous:raise ValueError("授权文件已被另一操作修改，请检查账号。")
            fd,temp=tempfile.mkstemp(dir=path.parent)
            try:
                with os.fdopen(fd,"w",encoding="utf8") as f:
                    json.dump(credentials,f);f.flush();os.fsync(f.fileno())
                os.replace(temp,path)
            finally:Path(temp).unlink(missing_ok=True)
        return
    raise ValueError("设备授权已过期。")

if __name__=="__main__":
    import tkinter as tk
    from tkinter import messagebox
    root=tk.Tk();root.withdraw()
    def consent(url,code):
        webbrowser.open(url)
        messagebox.showinfo("百度官方授权","请在刚打开的百度页面输入设备码 "+code+"。完成授权后关闭此提示继续。",parent=root)
    try:authorize(consent);messagebox.showinfo("百度网盘","新账号授权已保存。",parent=root)
    except Exception as e:messagebox.showerror("百度网盘",str(e) if isinstance(e,ValueError) else "请检查应用设置、文件路径或网络。",parent=root)
    finally:root.destroy()
