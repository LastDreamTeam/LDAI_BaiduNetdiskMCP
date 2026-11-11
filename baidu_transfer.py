# SPDX-License-Identifier: AGPL-3.0-only
"""Baidu Open Platform block uploads and bounded, paced downloads."""
import hashlib
import http.client
import json
import os
from pathlib import PurePosixPath
import time
from urllib.parse import urlencode
from uuid import uuid4
from open_platform import token_value

BLOCK=4*1024*1024

def scoped(path):
    root=os.environ.get("BAIDU_OAUTH_ROOT","/").rstrip("/") or "/"
    for value in (root,path):
        if not value.startswith("/") or value.startswith("//") or "\\" in value or ".." in value.split("/") or any(ord(c)<32 for c in value):
            raise ValueError("Use an absolute account path without traversal.")
    p=PurePosixPath(path)
    if not p.is_relative_to(PurePosixPath(root)):raise ValueError("Path outside this account's configured directory.")
    return str(p)

class Pacer:
    def __init__(self,kib):
        self.rate=kib*1024
        self.deadline=time.monotonic()
    def wait(self,n):
        if self.rate:
            self.deadline=max(self.deadline,time.monotonic())+n/self.rate
            time.sleep(max(0,self.deadline-time.monotonic()))

def post(host,path,params,body,content_type,pacer=None):
    conn=http.client.HTTPSConnection(host,timeout=60)
    try:
        conn.putrequest("POST",path+"?"+urlencode(params))
        conn.putheader("Content-Type",content_type);conn.putheader("Content-Length",str(len(body)));conn.endheaders()
        chunk_size=min(65536,max(1024,pacer.rate//4)) if pacer and pacer.rate else 65536
        for i in range(0,len(body),chunk_size):
            chunk=body[i:i+chunk_size]
            if pacer:pacer.wait(len(chunk))
            conn.send(chunk)
        resp=conn.getresponse();raw=resp.read(1048577)
        if resp.status!=200 or len(raw)>1048576:raise ValueError("Baidu transfer HTTP failure.")
        val=json.loads(raw)
        if val.get("errno",0)!=0 or val.get("error_code",0)!=0:raise ValueError("Baidu rejected this upload step.")
        return val
    finally:conn.close()

def upload(source,target,kib):
    target=scoped(target);token=token_value();pacer=Pacer(kib)
    if not source.is_file() or source.stat().st_size<=0:raise ValueError("Select a nonempty regular file.")
    with source.open("rb") as f:
        chunks=[hashlib.md5(part).hexdigest() for part in iter(lambda:f.read(BLOCK),b"")]
        size=f.tell()
        if len(chunks)>10000:raise ValueError("Too many upload blocks.")
        base={"path":target,"size":size,"isdir":0,"block_list":json.dumps(chunks,separators=(",",":")),"rtype":0}
        def xpan(method,params):
            return post("pan.baidu.com","/rest/2.0/xpan/file",{"method":method,"access_token":token},urlencode(params).encode(),"application/x-www-form-urlencoded")
        prep=xpan("precreate",{**base,"autoinit":1})
        if prep.get("return_type")==2:
            return {"path":target,"bytes":size,"instant_upload":True,"provider_accepted":True}
        upload_id=prep.get("uploadid");indices=prep.get("block_list")
        if not upload_id or not isinstance(indices,list):raise ValueError("Provider did not allocate upload blocks.")
        for i in indices:
            if not isinstance(i,int) or not 0<=i<len(chunks):raise ValueError("Invalid requested block.")
            f.seek(i*BLOCK);part=f.read(BLOCK)
            if hashlib.md5(part).hexdigest()!=chunks[i]:raise ValueError("File changed during upload.")
            bound=uuid4().hex
            body=(f'--{bound}\r\nContent-Disposition: form-data; name="file"; filename="block"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()+part+f"\r\n--{bound}--\r\n".encode()
            reply=post("d.pcs.baidu.com","/rest/2.0/pcs/superfile2",{"method":"upload","type":"tmpfile","access_token":token,"path":target,"uploadid":upload_id,"partseq":i},body,"multipart/form-data; boundary="+bound,pacer)
            if reply.get("md5","").lower()!=chunks[i]:raise ValueError("Baidu block verification failed.")
        result=xpan("create",{**base,"uploadid":upload_id})
        return {"path":result.get("path",target),"bytes":result.get("size",size),"file_id":result.get("fs_id"),"provider_accepted":bool(result.get("fs_id"))}
