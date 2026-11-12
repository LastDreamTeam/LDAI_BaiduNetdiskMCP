# SPDX-License-Identifier: AGPL-3.0-only
"""End-to-end downstream authorization with a fixture Baidu code exchange."""
import asyncio
import base64
import hashlib
import json
import re
import secrets
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
import httpx
from key_value.aio.stores.memory import MemoryStore
import native_pan_server as mod

async def test(native=False):
    origin='https://bridge.example.org'
    callback='https://chatgpt.com/connector_platform_oauth_redirect'
    web_callback = callback
    if native:
        callback = "http://127.0.0.1:54053/callback"
    identities={'first':'100001','second':'100002','same-owner-readonly':'100001'}
    async def lookup(token,ep,params,*,form=None):
        uid=identities[token.removeprefix('fixture.').removesuffix('.new')]
        if form is not None:
            assert params['method']=='create' and form['rtype']=='0'
            return {'errno':0,'path':form['path'],'fs_id':42,'isdir':1}
        return {'errno':0,'uk':uid} if ep=='nas' else {'errno':0,'list':[{'path':params.get('dir','/')+'/item','size':3}]}
    raw_requests = []
    enriched = set()
    async def baidu_endpoint(request):
        params=dict(request.url.params)
        assert request.method=="GET" and request.url.host=="pan.baidu.com" and request.url.path=="/xpan/oauth/get"
        assert params["client_id"]=="fixture-client" and params["client_secret"]==app_secret
        if params["method"]=="token":
            who=params["code"]
            assert params["redirect_uri"]==origin+"/pan/callback"
            scopes="nd_open_read" if who=="same-owner-readonly" else "nd_open_read,nd_open_write"
            raw={"access_token":"fixture."+who,"refresh_token":"refresh."+who,"expires_in":3600,
                 "scope":scopes,"authorized_paths":["/"+who]}
        else:
            assert params["method"]=="refresh_token"
            who="second" if params["refresh_token"]=="rotated.first-enrichment" else params["refresh_token"].removeprefix("refresh.")
            raw={"access_token":"fixture."+who+".new","refresh_token":"rotated."+who,"expires_in":3600,
                 "scope":"nd_open_read","authorized_paths":["/"+who+"/narrow"]}
        raw_requests.append(params["method"])
        if who == "second" and params["method"] == "token":
            partial = {k: raw[k] for k in ("access_token","refresh_token","authorized_paths")}
            return httpx.Response(200,json={"errno":0,"data":partial})
        if who == "second" and params["method"] == "refresh_token" and who not in enriched:
            enriched.add(who)
            raw.update(access_token="fixture.second",refresh_token="rotated.first-enrichment",scope=["nd_open_read","nd_open_write"],authorized_paths=["/second"])
            return httpx.Response(200,json={"errno":0,"data":raw})
        if who == "second":
            raw["scope"] = raw["scope"].split(",")
            return httpx.Response(200, json=raw)
        return httpx.Response(200,json={"errno":"0" if who == "same-owner-readonly" else 0,"data":raw})
    from types import SimpleNamespace
    upstream_http=httpx.MockTransport(baidu_endpoint)
    mocked_http=SimpleNamespace(AsyncClient=lambda **kw:httpx.AsyncClient(transport=upstream_http,**kw), HTTPError=httpx.HTTPError)
    app_secret=secrets.token_urlsafe(32)
    with tempfile.TemporaryDirectory() as folder:
        policy=Path(folder)/'roots.json'
        policy.write_text(json.dumps({'default_roots':['/example'], 'follow_directory_grants':True}),encoding='utf8')
        server=mod.build_server(origin,web_callback,'fixture-client',app_secret,policy,storage=MemoryStore())
        server.auth._token_validator.lookup=lookup
        app=mod.http_app(server)
        transport=httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app), httpx.AsyncClient(transport=transport,base_url=origin) as control:
            reg=await control.post('/pan/register',json={'client_name':'Fixture client','redirect_uris':[callback],'grant_types':['authorization_code','refresh_token'],'response_types':['code'],'token_endpoint_auth_method':'none','scope':'netdisk'})
            assert reg.status_code==201,('registration',reg.status_code,reg.text[:180])
            cid=reg.json()['client_id']
            async def grant(who):
                async with httpx.AsyncClient(transport=transport,base_url=origin) as c:
                    verifier=secrets.token_urlsafe(48)
                    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
                    state=secrets.token_urlsafe(16)
                    res=await c.get('/pan/authorize',params={'client_id':cid,'redirect_uri':callback,'response_type':'code','scope':'netdisk','state':state,'resource':origin+'/pan/mcp','code_challenge':challenge,'code_challenge_method':'S256'})
                    assert res.is_redirect,('authorize',res.status_code,res.text[:100])
                    loc=res.headers['location']
                    txn=parse_qs(urlsplit(loc).query)['txn_id'][0]
                    form=await c.get(loc)
                    m=re.search(r'name="csrf_token"\s+value="([^"]+)"',form.text)
                    assert m,('consent',form.status_code)
                    agreed=await c.post('/pan/consent',data={'txn_id':txn,'action':'approve','csrf_token':m[1]})
                    assert agreed.is_redirect,('consent submit',agreed.status_code)
                    target=urlsplit(agreed.headers['location']); up=parse_qs(target.query)
                    assert up['show_dir']==['1'] and up['response_type']==['code']
                    assert 'client_secret' not in up
                    assert up['state'][0].startswith('ldpan1.directory_v2_')
                    assert target.hostname=='pan.baidu.com' and target.path=='/union/oauth/authorize' and up['scope']==['nd_open_read,nd_open_write'] and up['redirect_uri']==[origin+'/pan/callback']
                    done=await c.get('/pan/callback',params={'state':up['state'][0],'code':who})
                    assert done.is_redirect,('callback',done.status_code)
                    reply=urlsplit(done.headers['location']); params=parse_qs(reply.query)
                    assert reply[:3] == urlsplit(callback)[:3] and params['state']==[state] and params['iss']==[origin+'/pan']
                    response=await c.post('/pan/token',data={'code':params['code'][0],'client_id':cid,'grant_type':'authorization_code','redirect_uri':callback,'code_verifier':verifier,'resource':origin+'/pan/mcp'})
                    assert response.status_code==200,('token',response.status_code,response.text[:160])
                    token=response.json()['access_token']
                    assert token!='fixture.'+who
                    async def call(t,name,args):
                        r=await c.post('/pan/mcp',headers={'Authorization':'Bearer '+t,'Accept':'application/json, text/event-stream'},
                            json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':args}})
                        assert r.status_code==200,(name,r.status_code)
                        return r.json()['result']
                    profile=await call(token,'baidu_profile',{})
                    assert profile['structuredContent']['id']==identities[who]
                    caps=(await call(token,'baidu_share_capabilities',{}))['structuredContent']
                    assert caps['read_roots']==['/'+who]
                    assert caps['share_roots']==([] if who=='same-owner-readonly' else ['/'+who])
                    assert (await call(token,'baidu_list',{'path':'/'}))['isError']
                    assert not (await call(token,'baidu_list',{'path':'/'+who})).get('isError')
                    assert (await call(token,'baidu_create_directory',{'path':'/outside/new'}))['isError']
                    created=await call(token,'baidu_create_directory',{'path':'/'+who+'/new'})
                    assert bool(created.get('isError'))==(who=='same-owner-readonly')
                    refreshed=await c.post('/pan/token',data={'grant_type':'refresh_token','refresh_token':response.json()['refresh_token'],'client_id':cid,'resource':origin+'/pan/mcp'})
                    assert refreshed.status_code==200,refreshed.status_code
                    newer=refreshed.json()['access_token']
                    caps=(await call(newer,'baidu_share_capabilities',{}))['structuredContent']
                    assert caps['read_roots']==['/'+who+'/narrow'] and caps['share_roots']==[]
                    assert (await call(token,'baidu_list',{'path':'/'+who}))['isError']
            async def available(token):return True
            with patch.object(mod,'httpx',mocked_http),patch.object(mod,'provider_request',lookup),patch.object(mod,'official_share_call',available):
                await asyncio.gather(*(grant(who) for who in identities))
            assert raw_requests.count('token')==3 and raw_requests.count('refresh_token')==4
    print('PASS: browser DCR/PKCE, native directory token exchange, same-account connection isolation, read/write boundaries and refresh narrowing (simulated Baidu).')
if __name__=='__main__':
    asyncio.run(test())
    asyncio.run(test(native=True))
