"""21:25 deterministic counts from durable curation journals; no model calls."""
import os
import datetime,importlib.util,json,pathlib,subprocess,sys
ROOT=pathlib.Path('/srv/wiki/stack');TZ=datetime.timezone(datetime.timedelta(hours=8))
spec=importlib.util.spec_from_file_location('vc',ROOT/'vault-client.py');vc=importlib.util.module_from_spec(spec);spec.loader.exec_module(vc)
now=datetime.datetime.now(TZ);date=now.date().isoformat();test='--test' in sys.argv
if not test and (now.hour,now.minute)<(21,25):raise SystemExit('Not the snapshot time')
creds=json.loads((ROOT/'credentials.json').read_text());c=vc.MCP(os.environ.get('WIKI_MCP_URL','https://wiki.example.invalid/mcp'),creds['mcp_token'],timeout=150)
path=('00-Test/v19-daily/' if test else '90-Logs/首次全量整理/日报/数据/')+date+'.md'
old=c.tool('read_note',path=path)
if not test and not old.startswith('Note not found:'):raise SystemExit('Already generated for '+date)
code="import fs from 'node:fs';\nconst d='/app/curation-results/pipeline',entries=[];\n// Read one receipt at a time; never retain copied original texts from every commit.\nfor(const n of fs.readdirSync(d).filter(n=>/^(commit-|segment-|maintenance).*\\.json$/.test(n))){\n const j=JSON.parse(fs.readFileSync(d+'/'+n));let value;\n if(n==='maintenance.json')value={classification_corrections:j.classification_corrections};\n else if(n.startsWith('segment-'))value={chunks:(j.chunks||[]).map(c=>({at:c.at}))};\n else value={status:j.status,at:j.at,completed_at:j.completed_at,result:j.result,prepared:{index:j.prepared?.index,initial:j.prepared?.initial,repair:j.prepared?.repair}};\n entries.push({key:n.replace(/\\.json$/,''),value});\n}\nconsole.log(JSON.stringify(entries));"
rows=json.loads(subprocess.check_output(['sudo','docker','exec','-i','obsidian-mcp','node','--input-type=module','-'],input=code,text=True,timeout=150))
def parse(t):return datetime.datetime.fromisoformat(t.replace('Z','+00:00')).astimezone(TZ)
maintenance=next((x['value'] for x in rows if x['key']=='maintenance'),{});corrections=maintenance.get('classification_corrections',{})
daily=[];progress=[];segments=0
for item in rows:
 j=item['value']
 if item['key'].startswith('segment-'):
  for chunk in j.get('chunks',[]):
   if chunk.get('at'):
    at=parse(chunk['at']);progress.append(at)
    if at.date()==now.date():segments+=1
 if not item['key'].startswith('commit-') or j.get('status')!='PASS':continue
 at=parse(j.get('completed_at')or j['at']);progress.append(at)
 if at.date()==now.date():daily.append((item['key'][7:],j))
status=json.loads(c.tool('curation_status'));initial=[j['prepared']['index'] for k,j in daily if j['prepared'].get('initial')]
data={'date':date,'cutoff':now.isoformat(),'queue_start':min(initial) if initial else status['next_index'],'queue_end':status['next_index'],'total':status['total'],
 'knowledge':sum(j['result'].get('knowledge_successes',0) for k,j in daily),'no_knowledge':sum(j['result']['disposition']=='no_knowledge' and not j['prepared'].get('repair') and k not in corrections for k,j in daily),
 'repairs':sum(j['result'].get('repair_successes',0) for k,j in daily),'reading_segments':segments,'success_total':status['successes'],'repair_total':status['repairs'],
 'no_progress_48h':bool(progress) and (now-max(progress)).total_seconds()>=172800,'last_progress':max(progress).isoformat() if progress else None,
 'unfinished_commits':[{'key':x['key'],'status':x['value'].get('status'),'at':x['value'].get('at')} for x in rows if x['key'].startswith('commit-') and x['value'].get('status')not in ['PASS']],
 'scope':'Today Asia/Shanghai to cutoff. Reclassified old no_knowledge excluded; historical receipts retained.','test':test}
body='# 知识库日报统计数据\n\n```json\n'+json.dumps(data,ensure_ascii=False,indent=2)+'\n```\n'
c.tool('write_note',path=path,content=body);assert c.tool('read_note',path=path).split('\n\n---\n\n',1)[1]==body
print(json.dumps({'path':path,'data':data},ensure_ascii=False))
