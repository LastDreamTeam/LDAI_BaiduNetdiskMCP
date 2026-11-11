# SPDX-License-Identifier: AGPL-3.0-only
"""Exercise the stdio entry point with two independent, synthetic account workers."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
PRODUCT=json.loads((ROOT/"connector.json").read_text())
FAKE="""
import json,os,sys
name=os.environ['TEST_TOOL']
for line in sys.stdin:
    r=json.loads(line)
    if 'id' not in r:continue
    m=r['method']
    if m=='initialize':
        result={'protocolVersion':'2025-06-18','capabilities':{'tools':{}},'serverInfo':{'name':'test','version':'1.0.0'}}
    elif m=='tools/list':
        result={'tools':[{'name':n,'inputSchema':{'type':'object'}} for n in [name,'other_provider_test']]}
    elif m=='tools/call':
        result={'content':[{'type':'text','text':os.environ['TEST_ACCOUNT']}],'isError':False}
    print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':result}),flush=True)
"""

class Isolation(unittest.TestCase):
    def test_clients_do_not_switch_each_others_account(self):
        tool=PRODUCT["toolPrefixes"][0]+"test"
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)
            helper=p/"worker.py";helper.write_text(FAKE,encoding="utf8")
            cfg=p/"accounts.json"
            cfg.write_text(json.dumps({"connector":PRODUCT["id"],"accounts":{
                name:{"command":sys.executable,"args":[str(helper)],"env":{"TEST_TOOL":tool,"TEST_ACCOUNT":name}}
                for name in ["work","personal"]}}),encoding="utf8")
            with subprocess.Popen([sys.executable,str(ROOT/"router.py")],env={**os.environ,"LDAI_ACCOUNTS_FILE":str(cfg)},
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,encoding="utf8") as child:
                def req(n,m,params):
                    child.stdin.write(json.dumps({"jsonrpc":"2.0","id":n,"method":m,"params":params})+"\n")
                    child.stdin.flush()
                    return json.loads(child.stdout.readline())["result"]
                try:
                    self.assertEqual(req(1,"initialize",{})["serverInfo"]["name"],PRODUCT["id"])
                    names={x["name"] for x in req(2,"tools/list",{})["tools"]}
                    self.assertEqual(names,{"ldai_accounts","ldai_manage_accounts","work__"+tool,"personal__"+tool})
                    for i,alias in enumerate(["work","personal","work"]):
                        reply=req(3+i,"tools/call",{"name":alias+"__"+tool,"arguments":{}})
                        self.assertEqual(reply["content"][0]["text"],alias)
                    self.assertTrue(req(8,"tools/call",{"name":"other_provider_test","arguments":{}})["isError"])
                finally:
                    child.stdin.close()
                    try:child.wait(timeout=8)
                    except subprocess.TimeoutExpired:child.kill();child.wait()
