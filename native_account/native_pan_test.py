# SPDX-License-Identifier: AGPL-3.0-only
import asyncio
import json
import tempfile
from pathlib import Path
from key_value.aio.stores.memory import MemoryStore
import httpx
from fastmcp import Client, FastMCP
from native_pan_server import BaiduTokenVerifier, register_read_tools, build_server, http_app

async def run():
    identities={'sample-first':'100001','sample-second':'100002','sample-first-refresh':'100001'}
    async def lookup(token,endpoint,params):
        if token not in identities:raise ValueError('denied')
        identity=identities[token]
        if endpoint=='nas':return {'errno':0,'uk':identity,'netdisk_name':'Sample '+identity}
        return {'errno':0,'list':[{'path':'/'+identity,'size':1,'other':'not returned'}]}
    verifier=BaiduTokenVerifier('example',lookup)
    assert await verifier.verify_token('expired') is None
    actual=await asyncio.gather(*(verifier.verify_token(t) for t in identities))
    assert actual[0].claims['profile']['id']==actual[2].claims['profile']['id']
    async def per_account(grant):
        server=FastMCP('Fixture');register_read_tools(server,lambda:grant,lookup,lambda uid:['/'])
        async with Client(server) as c:
            tools={t.name:t for t in await c.list_tools()}
            assert tools['baidu_profile'].meta['openai/profile'] is True
            assert tools['baidu_profile'].outputSchema['required']==['id']
            profile=(await c.call_tool('baidu_profile',{})).structured_content
            files=(await c.call_tool('baidu_list',{})).structured_content
            assert files['items']==[{'path':'/'+profile['id'],'size':1}]
            return profile['id']
    assert await asyncio.gather(*(per_account(t) for t in actual))==['100001','100002','100001']
    origin='https://bridge.example.org'
    temporary=tempfile.TemporaryDirectory()
    policy=Path(temporary.name)/'roots.json'
    policy.write_text(json.dumps({val:['/'] for val in identities.values()}),encoding='utf8')
    server=build_server(origin,'https://chatgpt.com/connector_platform_oauth_redirect','example-client','fixture-only-strong-random-looking-credential-standin', policy, storage=MemoryStore())
    app=http_app(server)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=origin) as c:
            meta=(await c.get('/.well-known/oauth-authorization-server/pan')).json()
            assert meta['issuer']==origin+'/pan' and meta['authorization_endpoint']==origin+'/pan/authorize'
            assert 'S256' in meta['code_challenge_methods_supported'] and meta.get('registration_endpoint')
            res=await c.post('/pan/mcp',json={'jsonrpc':'2.0','id':1,'method':'initialize','params':{}})
            assert res.status_code==401 and 'resource_metadata=' in res.headers['www-authenticate']
            from urllib.parse import urlsplit
            marker=res.headers['www-authenticate'].split('resource_metadata="',1)[1].split('"',1)[0]
            assert (await c.get(urlsplit(marker).path)).json()['resource']==origin+'/pan/mcp'
            # Only the reference-token validator and Baidu network are fixtures.
            # Run both tools concurrently through one authenticated MCP HTTP endpoint.
            from unittest.mock import patch
            import native_pan_server
            server.auth.load_access_token=verifier.verify_token
            async def invoke(token,name,args):
                r=await c.post('/pan/mcp',headers={'Authorization':'Bearer '+token,
                    'Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2025-06-18'},
                    json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':args}})
                assert r.status_code==200,(r.status_code,name)
                result=r.json()['result'];assert not result.get('isError'),name
                return result['structuredContent']
            with patch.object(native_pan_server,'provider_request',lookup):
                pairs=await asyncio.gather(*(invoke(token,name,{}) for token in identities for name in ('baidu_profile','baidu_list')))
            for i,token in enumerate(identities):
                assert pairs[2*i]['id']==identities[token] and pairs[2*i+1]['items'][0]['path']=='/'+identities[token]
            with patch.object(native_pan_server,'provider_request',lookup):
                policy.write_text(json.dumps({'100002':['/restricted']}),encoding='utf8')
                for token in list(identities):
                    result=await c.post('/pan/mcp',headers={'Authorization':'Bearer '+token,'Accept':'application/json, text/event-stream'},
                        json={'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'baidu_list','arguments':{'path':'/'}}})
                    assert result.json()['result']['isError'] is True
            denied=await c.post('/pan/mcp',headers={'Authorization':'Bearer expired'},json={'jsonrpc':'2.0','id':1,'method':'tools/list'})
            assert denied.status_code==401

    temporary.cleanup()
    print('Passed: MCP discovery and 401 challenge, two provider identity fixtures, grant refresh stability, isolation of returned listings, invalid grant rejection.')
if __name__=='__main__':asyncio.run(run())
