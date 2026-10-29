#!/usr/bin/env python3
"""Read durable curation journals; never confuse submission with ingestion."""
import hashlib,json,os,pathlib,time,re
from kb_receipt_reader import selected_receipt,FIELDS
P=pathlib.Path('/srv/wiki/stack/curation-results/pipeline');R=pathlib.Path('/var/lib/kb-confirm')
def read(p,default=None):
 try:return json.loads(p.read_text())
 except FileNotFoundError:return default
def classify(path,receipts,question_id=None,question_version=None):
 good=[x for x in receipts if x.get('status')=='PASS'and x.get('maintenance_finalized')and path in x.get('sources',[])and(question_id is None or x.get('question_id')==question_id)and(question_version is None or str(x.get('question_version'))==str(question_version))]
 if not good:return {'state':'submitted','label':'已写入投稿箱，尚未入库','reason':'尚无与这份答复路径相符的最终 PASS 回执；等待每小时串行整理核对。','pages':[]}
 last=max(good,key=lambda x:x.get('at')or'')
 if last.get('disposition')!='knowledge':return {'state':'closed','label':'已核对结案（未写知识正文）','reason':last.get('reason')or'处置为非知识；以整理记录为准','pages':[],'commit_key':last['key'],'record':last.get('record')}
 return {'state':'ingested','label':'已入库','reason':'最终提交回执 PASS 且 maintenance_finalized=true；不代表模型语义自动无误。','pages':last.get('pages',[]),'commit_key':last['key'],'at':last.get('at'),'record':last.get('record')}

def confirmation_identity(j):
 qid=j.get('prepared.confirmation.question_id');version=j.get('prepared.confirmation.question_version')
 if qid and version is not None:return qid,version
 # Old journals retained the complete original response, before confirmation metadata existed.
 paths=j.get('prepared.record.paths',[])
 for s in j.get('prepared.snapshots',[]):
  text=s.get('text','')
  if s.get('path')not in paths or not s.get('path','').startswith('00-Inbox/待确认答复/')or hashlib.sha256(text.encode()).hexdigest()!=s.get('sha256'):continue
  m=re.search(r'```json\s*\n(.*?)\n```',text,re.S)
  if not m:continue
  try:a=json.loads(m[1])
  except ValueError:continue
  if a.get('kind')=='confirmation-response'and a.get('question_id')and a.get('question_version')is not None:return a['question_id'],a['question_version']
 return None,None
def refresh():
 state=read(R/'ingestion.json',{});cached=state.get('journals',{});out={}
 audit=selected_receipt(P/'maintenance.json',(('daily_commit_audit',),)).get('daily_commit_audit',{})
 legacy=read(R/'historical-receipt-keys.json',[])
 keys=set(k for k,v in audit.items()if v.get('confirmation_question_id'))|set(legacy)
 for key in keys:
  if not re.fullmatch(r'[0-9a-f]{64}',key):continue
  p=P/('commit-'+key+'.json')
  if not p.is_file():continue
  stat=p.stat();sig=[stat.st_mtime_ns,stat.st_size]
  if cached.get(key,{}).get('sig')==sig:out[key]=cached[key];continue
  j=selected_receipt(p,FIELDS+(('prepared','snapshots'),)if key in legacy else FIELDS)
  qid,version=confirmation_identity(j)
  out[key]={'key':key,'sig':sig,'status':j.get('status'),'maintenance_finalized':j.get('maintenance_finalized',False),'sources':j.get('prepared.record.paths',[]),'question_id':qid,'question_version':version,'pages':j.get('result.knowledge_paths',[]),'disposition':j.get('result.disposition'),'at':j.get('completed_at')or j.get('at'),'record':j.get('plan.recordPath'),'reason':j.get('input.reason')}
  del j
 forcards={}
 for f in(R/'items').glob('q-*.json'):
  it=read(f);change=it.get('change')or{};path=change.get('file')
  if path:
   q=it.get('payload')or{}
   qid=it.get('question_id')or q.get('qid')
   version=it.get('question_version')or q.get('version')
   if not qid or version is None:
    forcards[it['id']]={'state':'submitted','label':'已写入投稿箱，尚未完成身份核验','reason':'卡片缺 question_id 或 question_version，不能仅凭路径宣称已入库。','pages':[]}
   else:forcards[it['id']]=classify(path,list(out.values()),qid,version)
 result={'at':time.time(),'cards':forcards,'journals':out}
 p=R/'ingestion.json';tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(result,ensure_ascii=False));os.chmod(tmp,0o600);tmp.replace(p)
 return {'cards':len(forcards),'ingested':sum(x['state']=='ingested'for x in forcards.values()),'submitted':sum(x['state']=='submitted'for x in forcards.values())}
if __name__=='__main__':
 import resource,signal
 resource.setrlimit(resource.RLIMIT_AS,(256*1024**2,256*1024**2));signal.alarm(40)
 print(json.dumps(refresh(),ensure_ascii=False))
