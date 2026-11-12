import argparse,json,subprocess,sys,zipfile,shutil
from pathlib import Path
here=Path(__file__).resolve().parent

def pack(manifest,version,destination):
 root=here.parent
 data=json.loads((root/'connector.json').read_text(encoding='utf8'));slug=data['id'];pbase='skills/'+slug
 app=json.loads(Path(manifest).read_text(encoding='utf8'));app['version']=version
 dest=Path(destination)
 sources=subprocess.check_output(['git','-C',str(root),'ls-files','-z']).decode().split('\0')
 included=[n for n in sources if n and ( ('/' not in n and (n.endswith(('.py','.mjs','.cjs')) or n in ('connector.json','package.json','package-lock.json','requirements.txt','LICENSE'))) or n.startswith(('lib/','src/','bridge/','scripts/'))) and n not in ['build_plugin.py','verify_release.py','client_config.py']]
 skill='''---
name: '''+slug+'''
description: 使用当前插件连接的账号，或调用明确指定的本机账号工具。
---

# '''+data['title']+'''

账号管理入口在本插件详情页的「管理」。从「已连接的账户」选择账号、添加、改名、重新连接或断开。读取时使用选中连接的原生工具。

只有用户明确选用本机账号时才使用下列工具。本机授权与浏览器所选连接分别管理。如果所选连接缺少能力，先说明操作需要的授权。
在有本地执行器和 Python 3.11+ 的客户端，从本技能目录运行 `python -X utf8 scripts/local_account_tools.py` 列出完整工具及参数。执行时传入精确工具名，把参数对象通过标准输入传入。根据工具返回检查是否成功，沿用其原有权限、路径、确认和传输限制。

源码和客户端安装说明见 ['''+data['title']+''']('''+data['repository']+''')。
'''
 with zipfile.ZipFile(dest,'w',zipfile.ZIP_DEFLATED) as z:
  z.writestr('.codex-plugin/plugin.json',json.dumps(app,ensure_ascii=False,indent=2)+'\n')
  original=(root/'skills'/slug/'SKILL.md').read_text(encoding='utf8').split('---',2)[-1].strip()
  z.writestr(pbase+'/SKILL.md',skill+'\n'+original+'\n')
  for n in included:z.write(root/n,pbase+'/scripts/'+n)
  z.write(here/'local_account_tools.py',pbase+'/scripts/local_account_tools.py')
 print(dest,'support',len(included))
 return dest
if __name__=='__main__':pack(*sys.argv[1:])
