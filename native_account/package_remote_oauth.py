# SPDX-License-Identifier: AGPL-3.0-only
"""Create an account-plugin overlay with a deployment-selected OAuth server."""
import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from zipfile import ZipFile, ZIP_DEFLATED

SERVER = 'baidu-account'
SCOPE = 'netdisk'
def package(current, url, version, revision, output, app_id=None):
    u=urlsplit(url)
    if u.scheme!='https' or not u.hostname or u.password or u.username or u.query or u.fragment or not u.path.endswith('/mcp'):
        raise ValueError('Expected an HTTPS MCP endpoint without credentials or query parameters.')
    def data(path):return json.loads(current[path])
    portable=data('plugin.json')
    overlay=data('.codex-plugin/plugin.json')
    base=data('mcp.json')
    legacy=data('.mcp.json')
    old=portable['version']
    ver=r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)'
    if not re.fullmatch(ver,version) or tuple(map(int,version.split('.')))<=tuple(map(int,old.split('.'))):
        raise ValueError('Pick a newer release version.')
    if overlay['name']!=portable['name'] or overlay['version']!=old:
        raise ValueError('The two manifests must refer to the same release.')
    if not re.fullmatch('[a-f0-9]{40}',revision):
        raise ValueError('Expected a Git commit for this packaging code.')
    descriptor={'type':'streamable-http','url':url,'extensions':{'com.openai':{'auth':{'type':'oauth','client':{'mode':'dcr'},'baseScopes':[SCOPE],'defaultScopes':[SCOPE]}}}}
    base['mcpServers'][SERVER]=descriptor
    legacy['mcpServers'][SERVER]={'type':'http','url':url}
    for manifest in (portable,overlay):manifest['version']=version
    provenance=data('release-source.json')
    provenance['previous_package_source']={k:provenance[k] for k in ('commit','tag','version') if k in provenance}
    provenance.update(version=version,commit=revision)
    provenance.pop('tag',None)
    for interface in (portable['extensions']['com.openai']['interface'],overlay['interface']):
        interface['longDescription']='通过已连接百度账号列出授权目录和文件。本机已配置的账号可使用原有传输工具。源码见 '+portable['repository']+'，连接配置版本 '+revision[:8]+'。'
    skillpath='skills/baidu-netdisk/SKILL.md'
    base_skill = current[skillpath].split('当用户说「管理账号」')[0]
    skill=base_skill.split('用户在插件页连接百度账号后')[0].rstrip()+'''

用户在插件页连接百度账号后，使用 baidu-account 的 baidu_profile、baidu_allowed_directories 和 baidu_list 读取选中连接。多个连接时，使用本次请求指定的账号。遇到未授权或目录无权访问时请用户通过插件的连接入口完成授权。不要以本机账号替代所选连接，也不要将任一用户的凭据带入另一连接。

本机 stdio 工具保留其原有授权。若用户明确指定本机配置的账号，可在其权限内使用原有文件传输工具。新连接当前支持资料和目录读取。
'''
    files={'plugin.json':portable,'.codex-plugin/plugin.json':overlay,'mcp.json':base,'.mcp.json':legacy,'release-source.json':provenance}
    if app_id is not None:
        if not re.fullmatch(r'(?:asdk_app_|connector_|templated_apps_)[A-Za-z0-9][A-Za-z0-9_-]*', app_id):
            raise ValueError('Use the OpenAI App ID from the registered service.')
        bound = json.loads(current.get('.app.json', '{"apps": {}}'))
        existing = {item.get('id') for item in bound['apps'].values()}
        if existing and existing != {app_id}:
            raise ValueError('This plugin already declares a different App. Inspect its bindings before changing it.')
        if not existing:
            bound['apps'][SERVER] = {'id': app_id}
        portable['extensions']['com.openai']['apps'] = './.app.json'
        overlay['apps'] = './.app.json'
        files['.app.json'] = bound
    with ZipFile(output,'x',compression=ZIP_DEFLATED) as z:
        for name,item in files.items():z.writestr(name,json.dumps(item,ensure_ascii=False,indent=2)+'\n')
        z.writestr(skillpath,skill)
    with ZipFile(output) as z:
        assert z.testzip() is None
        assert json.loads(z.read('mcp.json'))['mcpServers'][SERVER]==descriptor

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('source',type=Path,help='JSON mapping of current private plugin source paths to their text')
    p.add_argument('url',help='Verified MCP HTTPS endpoint')
    p.add_argument('version');p.add_argument('revision');p.add_argument('output',type=Path)
    p.add_argument('--app-id', help='Verified OpenAI App ID for this deployment, copied from its registered connector.')
    a=p.parse_args()
    package(json.loads(a.source.read_text(encoding='utf-8-sig')),a.url,a.version,a.revision,a.output,a.app_id)
