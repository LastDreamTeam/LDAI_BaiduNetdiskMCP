# SPDX-License-Identifier: AGPL-3.0-only
"""Build a plugin from committed Git objects and record its exact source revision."""
import argparse
import io
import json
from pathlib import Path
import re
import subprocess
import zipfile
from verify_release import audit

ROOT=Path(__file__).resolve().parent
def git(*args):
    return subprocess.check_output(["git","-C",str(ROOT),*args])
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push",action="store_true",help="Publish main and the matching version tag to origin before packaging")
    parser.add_argument("--account-name",help="Preserve the existing private account package identifier")
    parser.add_argument("--output",type=Path)
    a=parser.parse_args()
    if a.account_name and not re.fullmatch(r"[a-z][a-z0-9-]{0,63}",a.account_name):
        parser.error("Invalid account package name.")
    if git("status","--porcelain","--untracked-files=normal").strip():
        parser.error("Commit or move local changes before packaging.")
    commit=git("rev-parse","HEAD").decode().strip()
    product=json.loads(git("show",commit+":connector.json"))
    tag="v"+product["version"]
    source=zipfile.ZipFile(io.BytesIO(git("archive","--format=zip",commit)))
    paths={n:source.read(n) for n in source.namelist() if not n.endswith("/")}
    for n in ["plugin.json",".codex-plugin/plugin.json"]:
        m=json.loads(paths[n])
        if m["version"]!=product["version"]:
            parser.error("Version mismatch between manifests and connector.json.")
        if a.account_name:
            m["name"]=a.account_name
            ui=m.get("interface") or m["extensions"]["com.openai"]["interface"]
            ui["shortDescription"]="账号版 · "+ui["shortDescription"]
            ui["longDescription"]=ui["longDescription"]+" 版本控制仓库 "+product["repository"]+"，固定提交 "+commit[:8]+"。"
        paths[n]=(json.dumps(m,ensure_ascii=False,indent=2)+"\n").encode()
    paths["release-source.json"]=(json.dumps({"repository":product["repository"],"version":product["version"],
                "commit":commit,"tag":tag,"package":a.account_name or product["id"]},indent=2)+"\n").encode()
    audit(paths)
    if a.push:
        if git("branch","--show-current").decode().strip()!="main":
            parser.error("Run --push on main.")
        remote=git("remote","get-url","origin").decode().strip().removesuffix(".git").replace("git@github.com:","https://github.com/")
        if remote!=product["repository"]:
            parser.error("origin does not match connector.json.")
        if git("tag","--list",tag).strip():
            if git("rev-parse",tag+"^{commit}").decode().strip()!=commit:
                parser.error("Version tag points to another commit. Bump the version.")
        else:
            git("tag",tag,commit)
        git("push","--atomic","origin","HEAD:refs/heads/main","refs/tags/"+tag)
        refs=git("ls-remote","origin","refs/heads/main","refs/tags/"+tag).decode().splitlines()
        if len(refs)!=2 or any(r.split()[0]!=commit for r in refs):
            parser.error("Published refs could not be verified.")
    out=(a.output or ROOT/"dist"/(product["id"]+"-"+product["version"]+".zip")).resolve()
    out.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED) as z:
        for n,b in sorted(paths.items()):
            info=zipfile.ZipInfo(n, date_time=(1980,1,1,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=0o644 << 16
            z.writestr(info,b)
    with zipfile.ZipFile(out) as z:
        if z.testzip() or set(z.namelist())!=set(paths):
            raise RuntimeError("Plugin archive verification failed.")
    print(out)
if __name__=="__main__":
    main()
