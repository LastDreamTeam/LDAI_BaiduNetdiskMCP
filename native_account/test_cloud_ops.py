import asyncio,json,secrets
from fastmcp import Client,FastMCP
from fastmcp.server.auth import AccessToken
from native_pan_server import register_cloud_tools
async def test():
 from native_pan_server import account_permissions, provider_paths
 assert provider_paths(["/ok","../invalid"])==[]
 def legacy(grant):
  t=AccessToken(token="fixture",client_id="test",scopes=["netdisk"],claims={"profile":{"id":"1"},"baidu_grant":grant})
  return account_permissions(t,lambda uid:["/"],lambda uid:["/apps/demo"],legacy_write_policy=lambda uid:["/apps/demo"])
 assert legacy({"scopes":["netdisk"]})["share_roots"]==["/apps/demo"]
 assert legacy({"scopes":["netdisk"]})["write_roots"]==["/apps/demo"]
 assert legacy({"scopes":["netdisk"],"paths":["/photos"]})["read_roots"]==["/photos"]
 assert not legacy({"scopes":["netdisk"],"paths":["/photos"]})["share_roots"]
 assert not legacy({"scopes":["netdisk"],"paths":[]})["read_roots"]
 assert not legacy({"scopes":["nd_open_read"],"paths":["/apps/demo"]})["share_roots"]
 assert not legacy({"scopes":["nd_open_read","nd_open_write"],"directory_flow":True})["read_roots"]

 for user in ('100001','100002'):
  root='/'+user
  token=AccessToken(token='sample-'+user,client_id='example',scopes=['netdisk'],claims={'profile':{'id':user}})
  used=[]
  async def lookup(t,endpoint,params):
   assert t==token.token
   if params['method']=='search':
    return {'list':[{'fs_id':1,'path':root+'/document','size':20,'private':'omit'}], 'has_more':0}
   ids=json.loads(params['fsids'])
   return {'list':[{'fs_id':i,'path':(root if i==1 else '/elsewhere')+'/document'} for i in ids]}
  async def gateway(t,args=None):
   assert t==token.token
   if args is None:return True
   assert len(args['pwd'])==4 and args['period']==7 and json.loads(args['fsid_list'])==['1']
   used.append(args);return {'link':'https://pan.baidu.com/s/example','pwd':args['pwd'],'period':7}
  server=FastMCP('Fixture')
  register_cloud_tools(server,lambda uid:['/'+uid],lambda uid:['/'+uid] if uid=='100001' else [],lambda:token,lookup,gateway)
  async with Client(server) as c:
   row=await c.call_tool('baidu_search',{'keyword':'doc','path':root})
   assert row.structured_content['items']==[{'fs_id':1,'path':root+'/document','size':20}]
   assert (await c.call_tool('baidu_search',{'keyword':'doc','path':'/'},raise_on_error=False)).is_error
   caps=(await c.call_tool('baidu_share_capabilities',{})).structured_content
   assert caps['provider_tool_available']
   for ids in (['2'],['1','2'],['1','1'],['x']):
    assert (await c.call_tool('baidu_create_share',{'file_ids':ids},raise_on_error=False)).is_error
   result=await c.call_tool('baidu_create_share',{'file_ids':['1']},raise_on_error=False)
   assert result.is_error==(user=='100002')
   assert len(used)==(1 if user=='100001' else 0)
 print('Passed cloud search, file metadata authorization, extraction code and isolated sharing policy.')
if __name__=='__main__':asyncio.run(test())
