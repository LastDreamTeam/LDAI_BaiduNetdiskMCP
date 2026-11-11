# SPDX-License-Identifier: AGPL-3.0-only
"""Render a private candidate from EXISTING private-plugin manifests and a verified OpenAI App ID.
No upload, network access, or changes to your installed plugin. Do not commit the output.
"""
import argparse,json,re
from pathlib import Path

def render(portable,overlay,product,app_id):
    if not re.fullmatch(r'(?:asdk_app_|connector_|templated_apps_)[A-Za-z0-9][A-Za-z0-9_-]*', app_id):
        raise ValueError('Expected the OpenAI App ID returned by discovery, not a provider ID or URL.')
    if portable.get('name')!=overlay.get('name') or portable.get('version')!=overlay.get('version'):
        raise ValueError('Mismatch between the two private plugin manifests.')
    if not portable.get('repository','').rstrip('/').endswith('/'+product['repository'].rstrip('/').split('/')[-1]):
        raise ValueError('Plugin manifests do not belong to this connector.')
    def copy(obj):return json.loads(json.dumps(obj))
    p,o=copy(portable),copy(overlay)
    ext=p.setdefault('extensions',{}).setdefault('com.openai',{})
    if ext.get('apps') or o.get('apps'):
        raise ValueError('Already has an App reference; inspect the existing dependency before editing.')
    ext['apps']='./.app.json';o['apps']='./.app.json'
    return {'plugin.json':p,'.codex-plugin/plugin.json':o,'.app.json':{'apps':{product['id']:{'id':app_id}}}}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive_root',type=Path,help='Unpacked current private plugin archive')
    parser.add_argument('app_id',help='Already verified with OpenAI discovery for this exact service')
    parser.add_argument('output',type=Path,help='New private directory outside any Git checkout')
    a=parser.parse_args();dest=a.output.resolve()
    if any((p/'.git').exists() for p in [dest,*dest.parents]) or dest.is_relative_to(a.archive_root.resolve()):
        parser.error('Use an independent private output directory outside the source and package.')
    def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
    root=Path(__file__).resolve().parents[1]
    product=read(root/'connector.json');product['repository']=read(root/'plugin.json')['repository']
    files=render(read(a.archive_root/'plugin.json'),read(a.archive_root/'.codex-plugin/plugin.json'),product,a.app_id)
    dest.mkdir(parents=True,mode=0o700,exist_ok=False)
    for name,body in files.items():
        p=dest/name;p.parent.mkdir(exist_ok=True);p.write_text(json.dumps(body,indent=2,ensure_ascii=False)+'\n',encoding='utf8')
    print('Private manifest overlay ready for review. This does not enable account selection on old stdio tools or install a plugin.')
