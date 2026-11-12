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
    identities={'first':'100001','second':'100002'}
    async def lookup(token,ep,params):
        uid=identities[token.removeprefix('fixture.')]
        return {'errno':0,'uk':uid} if ep=='nas' else {'errno':0,'list':[{'path':'/'+uid,'size':3}]}
    class Upstream:
        async def fetch_token(self,**kw):
            who=kw['code']
            assert kw['redirect_uri']==origin+'/pan/callback' and who in identities
            return {'access_token':'fixture.'+who,'refresh_token':'refresh.'+who,'expires_in':3600,'token_type':'Bearer','scope':'basic,netdisk'}
    @asynccontextmanager
    async def provider():
        yield Upstream()
    with tempfile.TemporaryDirectory() as folder:
        policy=Path(folder)/'roots.json'
        policy.write_text(json.dumps({v:['/'] for v in identities.values()}),encoding='utf8')
        server=mod.build_server(origin,web_callback,'fixture-client',secrets.token_urlsafe(32),policy,storage=MemoryStore())
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
                    assert up['state'][0].startswith('ldpan1.')
                    assert target.hostname=='openapi.baidu.com' and up['scope']==['basic,netdisk'] and up['redirect_uri']==[origin+'/pan/callback']
                    done=await c.get('/pan/callback',params={'state':up['state'][0],'code':who})
                    assert done.is_redirect,('callback',done.status_code)
                    reply=urlsplit(done.headers['location']); params=parse_qs(reply.query)
                    assert reply[:3] == urlsplit(callback)[:3] and params['state']==[state] and params['iss']==[origin+'/pan']
                    response=await c.post('/pan/token',data={'code':params['code'][0],'client_id':cid,'grant_type':'authorization_code','redirect_uri':callback,'code_verifier':verifier,'resource':origin+'/pan/mcp'})
                    assert response.status_code==200,('token',response.status_code,response.text[:160])
                    token=response.json()['access_token']
                    assert token!='fixture.'+who
                    for name in ('baidu_profile','baidu_list'):
                        r=await c.post('/pan/mcp',headers={'Authorization':'Bearer '+token,'Accept':'application/json, text/event-stream'},
                           json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':{}}})
                        assert r.status_code==200,('mcp',r.status_code)
                        result=r.json()['result'];assert not result.get('isError')
                        data=result['structuredContent']
                        assert (data['id'] if name=='baidu_profile' else data['items'][0]['path'].strip('/'))==identities[who]
            with patch.object(server.auth,'_upstream_oauth_client',provider),patch.object(mod,'provider_request',lookup):
                await asyncio.gather(*(grant(who) for who in identities))
    print('PASS: two full DCR/consent/callback/S256 token flows, separate accounts through signed MCP tokens; simulated Baidu responses.')
if __name__=='__main__':
    asyncio.run(test())
    asyncio.run(test(native=True))
