# SPDX-License-Identifier: AGPL-3.0-only
"""Check the public Git tree or plugin ZIP before distributing it."""
import ipaddress
import re
from pathlib import PurePosixPath
from urllib.parse import urlsplit

PUBLIC_HOSTS = {
    "agent-plugins.org", "cursor.com", "fsf.org", "github.com", "www.gnu.org", "www.w3.org",
    "pan.baidu.com", "yun.baidu.com", "hestiacp.com", "mailu.io", "www.yxtown.com",
    "yxtown.com", "files.pythonhosted.org",
}
SECRET = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[\w]{20,}|sk-[A-Za-z0-9_-]{20,}|oc_sk_[\w-]{14,}|ya29\.[\w-]{14,}|AKIA[A-Z0-9]{16})|-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")
URL = re.compile(r"(?:https?|wss?)://([a-z\d_.:@\[\]-]+)", re.I)
IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
MAIL = re.compile(r"[\w.+-]+@[\w.-]+\.(?:com|org|net|cn|io)\b", re.I)

def audit(files):
    for name, raw in files.items():
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or path.name in {
            "accounts.json","config.js","bridge.json","study.json",".env"} or path.suffix in {
            ".zip",".key",".pem",".log",".sqlite",".db",".pyc"} or any(p in {
            ".git",".venv","__pycache__","node_modules","profiles","data"} for p in path.parts):
            raise ValueError(f"Private or generated file in {name}.")
        for number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
            where = f"{name} line {number}"
            if SECRET.search(line):
                raise ValueError("Credential pattern in " + where)
            for literal in URL.findall(line):
                host = urlsplit("//" + literal.rstrip(":.")).hostname
                if host and not (host in PUBLIC_HOSTS or host in {"localhost","127.0.0.1","::1","example.org"}
                                 or host.endswith((".example.org",".test",".invalid"))):
                    raise ValueError("Unreviewed literal URL host in " + where)
            for value in IPV4.findall(line):
                try:
                    ip = ipaddress.IPv4Address(value)
                except ValueError:
                    continue
                if not ip.is_loopback:
                    raise ValueError("Literal network address in " + where)
            for value in MAIL.findall(line):
                if value.lower() != "git@github.com" and not value.lower().endswith("@example.org"):
                    raise ValueError("Account identifier in " + where)
