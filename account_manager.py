# SPDX-License-Identifier: AGPL-3.0-only
"""Desktop account manager. Authentication takes place on the user's machine."""
import contextlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

ROOT=Path(__file__).resolve().parent
PRODUCT=json.loads((ROOT/"connector.json").read_text(encoding="utf8"))
NAME=PRODUCT["id"]
ALIAS=re.compile(r"[a-z][a-z0-9_-]{0,15}\Z")
def location():
    return Path(os.environ.get("LDAI_ACCOUNTS_FILE") or Path.home()/".config"/"ldai-mcp"/NAME/"accounts.json").expanduser()
def load():
    path=location()
    if not path.exists():
        return {"connector":NAME,"accounts":{"default":{}}}
    data=json.loads(path.read_text(encoding="utf-8-sig"))
    if data.get("connector")!=NAME or not isinstance(data.get("accounts"),dict):
        raise ValueError("此账号配置不属于当前插件。")
    return data

@contextlib.contextmanager
def locked(file):
    file.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with file.open("a+b") as stream:
        if os.name=="nt":
            import msvcrt
            if stream.tell()==0:
                stream.write(b"0");stream.flush()
            stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_LOCK,1)
            try: yield
            finally:
                stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            fcntl.flock(stream,fcntl.LOCK_EX)
            try:yield
            finally:fcntl.flock(stream,fcntl.LOCK_UN)

def save(old,new):
    path=location()
    with locked(path.with_suffix(".lock")):
        if load()!=old:
            raise ValueError("另一个窗口已更新账号，请刷新后重试。")
        fd,tmp=tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd,"w",encoding="utf8") as f:
                json.dump(new,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
            os.replace(tmp,path)
        finally:
            Path(tmp).unlink(missing_ok=True)

def replace_entry(data,old,alias,entry):
    if not ALIAS.fullmatch(alias):
        raise ValueError("连接标识用 1–16 位小写英文、数字、短横线或下划线，以字母开头。")
    accounts=dict(data["accounts"])
    if alias!=old and alias in accounts:
        raise ValueError("这个标识已经存在。")
    if old is not None:del accounts[old]
    accounts[alias]=entry
    return {**data,"accounts":accounts}

def account_env(entry):
    env=dict(os.environ)
    for key in PRODUCT["providerEnvironment"]:env.pop(key,None)
    if entry.get("accountHome"):
        home=Path(entry["accountHome"]).expanduser()
        env.update(HOME=str(home),USERPROFILE=str(home),APPDATA=str(home/"AppData/Roaming"),LOCALAPPDATA=str(home/"AppData/Local"),
                   XDG_CONFIG_HOME=str(home/".config"),XDG_DATA_HOME=str(home/".local/share"),XDG_CACHE_HOME=str(home/".cache"))
        if NAME=="baidu-netdisk":env["BDPAN_CONFIG_DIR"]=str(home/".config/bdpan")
    env.update(entry.get("env",{}))
    return env

def gui():
    import tkinter as tk
    from tkinter import ttk,messagebox,filedialog
    import copy
    root=tk.Tk();root.title(PRODUCT["title"]+" · 账号管理");root.geometry("800x650")
    outer=ttk.Frame(root,padding=14);outer.pack(fill="both",expand=True)
    accounts=ttk.Treeview(outer,columns=("alias","kind"),show="tree headings",height=5)
    accounts.heading("#0",text="账号名称");accounts.heading("alias",text="工具标识");accounts.heading("kind",text="连接方式")
    accounts.pack(fill="x")
    form=tk.Canvas(outer,highlightthickness=0)
    scroll=ttk.Scrollbar(outer,command=form.yview);form.configure(yscrollcommand=scroll.set)
    buttons=ttk.Frame(outer);buttons.pack(side="bottom",fill="x",pady=8)
    scroll.pack(side="right",fill="y");form.pack(fill="both",expand=True)
    fields=ttk.Frame(form);form.create_window((0,0),window=fields,anchor="nw")
    fields.bind("<Configure>",lambda _:form.configure(scrollregion=form.bbox("all")))
    vars={}
    for i,(key,title) in enumerate([("alias","连接标识"),("label","显示名称"),*PRODUCT["managerFields"].items()]):
        ttk.Label(fields,text=title).grid(row=i,column=0,sticky="w",pady=3)
        v=vars[key]=tk.StringVar()
        ttk.Entry(fields,textvariable=v,width=50).grid(row=i,column=1,sticky="ew")
        if key.endswith(("_FILE","_DIR","_SCRIPT","_BIN","_JS")):
            def choose(k=key):
                path=filedialog.askdirectory() if k.endswith("_DIR") else filedialog.askopenfilename()
                if path:vars[k].set(path)
            ttk.Button(fields,text="选择",command=choose).grid(row=i,column=2)
    state={"data":None,"selected":None}
    def edit(alias=None):
        state["selected"]=alias
        entry=state["data"]["accounts"].get(alias,{})
        for key,var in vars.items():
            var.set((alias or "") if key=="alias" else entry.get("label",alias or "") if key=="label" else entry.get("env",{}).get(key,""))
    def reload():
        state["data"]=load()
        accounts.delete(*accounts.get_children())
        for alias,entry in state["data"]["accounts"].items():
            accounts.insert("","end",iid=alias,text=entry.get("label",alias),values=(alias,"已有适配器" if entry.get("command") else "原生"))
        edit()
    def select(_):
        selection=accounts.selection()
        if selection:edit(selection[0])
    accounts.bind("<<TreeviewSelect>>",select)
    def store():
        alias=vars["alias"].get().strip();old=state["selected"];data=state["data"]
        entry=copy.deepcopy(data["accounts"].get(old,{}))
        desired={k:v.get().strip() for k,v in vars.items() if k not in ("alias","label") and v.get().strip()}
        prior=entry.get("env",{})
        if entry.get("command") and any(desired.get(k)!=prior.get(k) for k in PRODUCT["managerFields"] if k not in entry.get("adapterFields",[])):
            if not messagebox.askyesno("切换连接方式","保存这些站点设置会将本账号切换到原生接口。继续吗？"):return None
            entry={};prior={}
        env={k:v for k,v in prior.items() if k not in PRODUCT["managerFields"]}
        env.update(desired);entry.update(label=vars["label"].get().strip() or alias,env=env)
        if not entry.get("command") and NAME in {"netease-music","baidu-netdisk","yxtown-study"} and not entry.get("accountHome") and (old is None or not prior):
            home=location().parent/"profiles"/uuid.uuid4().hex
            entry["accountHome"]=str(home)
            if NAME=="yxtown-study" and not env.get("YXTOWN_DATA_DIR"):
                env["YXTOWN_DATA_DIR"]=str(home/"study")
        if NAME=="yxtown-study" and not entry.get("command"):
            port=env.get("YXTOWN_PORT","19271")
            if any(a!=old and e.get("env",{}).get("YXTOWN_PORT","19271")==port for a,e in data["accounts"].items()):
                raise ValueError("为此浏览器账号选择一个独立桥接端口。")
        fresh=replace_entry(data,old,alias,entry)
        save(data,fresh);reload();accounts.selection_set(alias);edit(alias);return entry
    def disconnect():
        key=state["selected"]
        if key and messagebox.askyesno("断开账号","移除此插件中的连接？服务商上的账号和数据将保留。"):
            fresh=copy.deepcopy(state["data"]);del fresh["accounts"][key]
            save(state["data"],fresh);reload()
    def reconnect():
        entry=store()
        if not entry:return
        if entry.get("command") and entry.get("reconnectArgs"):
            cmd=entry["reconnectArgs"]
            if not isinstance(cmd,list) or not cmd or not all(isinstance(v,str) for v in cmd):raise ValueError("无有效授权助手。")
            subprocess.Popen(cmd,env=account_env(entry),creationflags=subprocess.CREATE_NEW_CONSOLE if os.name=="nt" else 0)
            return
        if entry.get("command"):
            messagebox.showinfo("已有适配器","这个账号使用原有授权适配器。可新增原生连接，或填写站点设置切换。");return
        env=account_env(entry)
        if entry.get("accountHome"):Path(entry["accountHome"]).mkdir(parents=True,exist_ok=True,mode=0o700)
        if NAME=="baidu-netdisk":
            helper=env.get("BDPAN_LOGIN_SCRIPT")
            if not helper or not Path(helper).is_file():raise ValueError("请选择官方 bdpan-storage 的 scripts/login.sh，并安装 Bash 和 bdpan。")
            binary=env.get("BDPAN_BIN")
            if binary:env["PATH"]=str(Path(binary).expanduser().resolve().parent)+os.pathsep+env.get("PATH","")
            argv=[shutil.which("bash") or "bash",Path(helper).resolve().as_posix()]
        elif NAME=="netease-music":
            if env.get("NCM_CLI_JS"):argv=[env.get("NCM_NODE") or "node",env["NCM_CLI_JS"],"login","--background"]
            else:
                exe=shutil.which("ncm-cli")
                if not exe:raise ValueError("安装官方 CLI 或选择 NCM_CLI_JS。")
                argv=[exe,"login","--background"]
        elif NAME=="yxtown-study":argv=["node",str(ROOT/"setup.mjs")]
        else:
            messagebox.showinfo("已保存","新请求将使用保存的服务地址和凭据文件。可用状态工具检查授权。");return
        # The interactive child needs a real terminal to show the provider consent URL or receive its code.
        if os.name=="nt":subprocess.Popen(argv,cwd=ROOT,env=env,creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
            terminal=shutil.which("x-terminal-emulator") or shutil.which("xterm")
            if terminal:subprocess.Popen([terminal,"-e",*argv],cwd=ROOT,env=env)
            else:raise ValueError("请在终端按 README 运行 profile_exec.py，完成此账号的官方授权。")
    def guard(fn):
        def call():
            try:fn()
            except Exception as e:messagebox.showerror("账号管理",str(e))
        return call
    ops=[("新增",lambda:edit()),("保存 / 改名",store),("重新连接",reconnect),("断开",disconnect),("刷新",reload)]
    if NAME=="hestiacp":
        from deletion import review
        ops.append(("审核删除",lambda:review(root)))
    for title,fn in ops:ttk.Button(buttons,text=title,command=guard(fn)).pack(side="left",padx=2)
    if "--check-ui" in sys.argv:
        root.withdraw();reload();root.update_idletasks();root.destroy();print("Account manager rendered.");return
    reload();root.mainloop()

def cli():
    import argparse
    parser=argparse.ArgumentParser(description="Manage named native provider accounts. Run without arguments for the desktop window.")
    commands=parser.add_subparsers(dest="action",required=True)
    commands.add_parser("list")
    add=commands.add_parser("add");add.add_argument("alias");add.add_argument("--label");add.add_argument("--env",action="append",default=[]);add.add_argument("--home")
    rename=commands.add_parser("rename");rename.add_argument("alias");rename.add_argument("new_alias")
    disconnect=commands.add_parser("disconnect");disconnect.add_argument("alias")
    a=parser.parse_args();data=load()
    if a.action=="list":
        print(json.dumps({k:v.get("label",k) for k,v in data["accounts"].items()},ensure_ascii=False));return
    if a.action=="add":
        env={}
        for pair in a.env:
            k,sep,value=pair.partition("=")
            if not sep or k not in PRODUCT["managerFields"]:parser.error("Unknown setup field.")
            env[k]=value
        entry={"env":env,"label":a.label or a.alias}
        if a.home:entry["accountHome"]=str(Path(a.home).expanduser().resolve())
        elif NAME in ("baidu-netdisk","netease-music"):entry["accountHome"]=str(location().parent/"profiles"/uuid.uuid4().hex)
        new=replace_entry(data,None,a.alias,entry)
    else:
        if a.alias not in data["accounts"]:parser.error("Unknown account.")
        if a.action=="rename":new=replace_entry(data,a.alias,a.new_alias,data["accounts"][a.alias])
        else:new={**data,"accounts":{k:v for k,v in data["accounts"].items() if k!=a.alias}}
    save(data,new)
if __name__=="__main__":
    if len(sys.argv)>1 and "--check-ui" not in sys.argv:cli()
    else:gui()
