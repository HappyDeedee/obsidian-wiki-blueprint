"""One receipt/queue calculation for both Inbox status entry points."""
import os
import collections,datetime,hashlib,importlib.util,json,pathlib,re,unicodedata,urllib.parse

PREFIXES=('00-Inbox/随手放/','00-Inbox/日记/')
OLD='90-Logs/资料整理/未整理.md'
TARGET='00-Inbox/整理状态.md'
def norm(p):return unicodedata.normalize('NFC',p.replace('\\','/')).lstrip('/')
def paths(text):return sorted(set(urllib.parse.unquote(x).removesuffix('.md')+'.md' for x in re.findall(r'\(obsidian://open\?vault=[^&\n]+&file=([^\n)]+)\)',text)))
def body(c,p):
 x=c.tool('read_note',path=p)
 if x.startswith('Note not found:'):return None
 if '\n\n---\n\n' not in x:raise RuntimeError('Invalid note response: '+p)
 return x.split('\n\n---\n\n',1)[1]
def fm(text):
 import yaml
 m=re.match(r'\A\ufeff?---\s*\n(.*?)\n---(?:\n|$)',text,re.S)
 return (yaml.safe_load(m[1]) or {}) if m else {}
def compute(c,bridge,format_state,previous_receipts=None):
 cache={};previous_receipts=previous_receipts or {};receipt_stamps={}
 def read(p):
  if p not in cache:
   old=previous_receipts.get(p,{})
   cache[p]=old['body'] if p in receipt_stamps and old.get('stamp')==receipt_stamps[p] else body(c,p)
  return cache[p]
 receipt_listing=c.tool('list_notes',folder='10-Sources/程序整理记录',limit=20000)
 if 'Omitted ' in receipt_listing or 'may be incomplete' in receipt_listing:raise RuntimeError('Receipt inventory incomplete')
 receipt_dates={}
 for line in receipt_listing.splitlines():
  match=re.match(r'^- (\S+) ',line)
  if match:
   for path in paths(line):receipt_dates[path]=match[1][:10];receipt_stamps[path]=match[1]
 inventory={}
 for prefix in PREFIXES:
  text=c.tool('list_notes',folder=prefix,limit=20000)
  if 'may be incomplete' in text or 'catching up' in text or 'Omitted ' in text:raise RuntimeError('Incomplete Inbox inventory')
  for p in paths(text):
   if p.startswith(prefix):
    t=read(p)
    if t is None:raise RuntimeError('Source changed during inventory')
    inventory[p]={'sha256':hashlib.sha256(t.encode()).hexdigest(),'text':t,'kind':'md'}
 attachments=bridge({'action':'list'})['entries']
 for entry in attachments:
  p=entry['path']
  if p.startswith(PREFIXES):inventory[p]={'entry':entry,'kind':'binary'}
 queue=json.loads(c.tool('curation_pending'))
 pending={norm(p) for row in queue.get('pending',[]) for p in row.get('paths',[])}
 blocked={norm(p) for row in queue.get('blocked',[]) for p in (row.get('paths') or [row.get('path','')])}
 receipt_cache={}
 def receipts(p,digest):
  term=pathlib.PurePosixPath(p).stem
  if term not in receipt_cache:
   offset=0;found=set()
   for _ in range(100):
    result=c.tool('search_notes',terms=[term],folder='10-Sources/程序整理记录',limit=100,offset=offset)
    marker=re.search(r'\[SearchPage (\{.*\})\]',result)
    if not marker:raise RuntimeError('Receipt search metadata missing: '+term)
    found.update(q for q in paths(result) if q.startswith('10-Sources/程序整理记录/'))
    next_offset=json.loads(marker[1]).get('next_offset')
    if next_offset is None:break
    if next_offset<=offset:raise RuntimeError('Receipt pagination stalled')
    offset=next_offset
   else:raise RuntimeError('Receipt search incomplete: '+term)
   receipt_cache[term]=[(q,read(q)) for q in sorted(found)]
  hits=[]
  for q,t in receipt_cache[term]:
   if not t:continue
   m=re.search(r'^版本：(.+)$',t,re.M)
   try:versions=json.loads(m[1]) if m else []
   except ValueError:continue
   if any(norm(x.get('path',''))==norm(p) and x.get('sha256')==digest for x in versions):hits.append((q,t))
  return hits
 groups={k:[] for k in ['已萃取','未萃取','只登记（音频等）','失败/挂起']};unmatched=[];roots=collections.Counter();resolved=[]
 state=format_state.get('files',{})
 for p,item in sorted(inventory.items()):
  digest=item.get('sha256');mapped=None;reason='';root='确实未处理或尚无正式回执'
  st=state.get(p,{})
  if item['kind']=='binary':
   e=item['entry'];identity=[e.get('mtime'),e.get('size'),e.get('revision')]
   digest=st.get('source_sha256') or st.get('registered_sha256') if st.get('identity')==identity else None
   if digest and st.get('output'):
    output=read(st['output'])
    if output:
     mapped=(st['output'],hashlib.sha256(output.encode()).hexdigest())
   root=st.get('status','无转换记录')
  hits=receipts(p,digest) if digest else []
  if not hits and mapped:
   hits=receipts(*mapped)
   if hits:reason='原件内容版本已绑定派生正文，按派生正文的正式回执匹配';resolved.append({'path':p,'via':mapped[0]})
  record={'path':p,'date':'暂无回执','wiki':[],'receipt':None,'reason':reason}
  if hits:
   q,t=max(hits,key=lambda pair:(receipt_stamps.get(pair[0],''),pair[0]));meta=fm(t);record['receipt']=q
   record['date']=receipt_dates.get(q,'回执已匹配（日期未知）')+'（整理回执保存日）'
   record['wiki']=sorted(set(re.findall(r'\[\[((?:20-Wiki|30-Projects)/[^]|#]+)(?:\|[^]]*)?\]\]',t)))
   status='已萃取' if meta.get('processed') is True and meta.get('disposition')=='knowledge' else '只登记（音频等）' if meta.get('disposition') in {'registered','registration','audio_registered','image_registered'} else '未萃取'
  else:
   status='未萃取'
   # Registration documents are not proof of semantic curation.  Keep them visible
   # in the registered group while preserving their original content untouched.
   if item['kind']=='md' and pathlib.PurePosixPath(p).name.startswith('登记-'):
    status='只登记（音频等）';record['reason']='历史压缩包附件登记页；没有正式知识回执，不计已萃取';root='压缩包附件登记页'
   elif item['kind']=='md' and pathlib.PurePosixPath(p).name=='使用说明.md':
    status='只登记（音频等）';record['reason']='入口使用说明，不是待萃取素材';root='目录使用说明'
   elif item['kind']=='binary' and (st.get('register_only') or st.get('status')=='registered_audio_no_transcription'):
    status='只登记（音频等）';record['reason']='已登记原件；未匹配正式知识回执'
   else:unmatched.append(p);roots[root]+=1
  if norm(p) in blocked:status='失败/挂起';record['reason']+='；队列阻塞'
  elif norm(p) in pending:record['reason']+='；当前队列待处理'
  groups[status].append(record)
 now=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(timespec='seconds')
 lines=['---','title: Inbox 整理状态','kind: generated','exclude_from_ingest: true','exclude_from_query: true','generated: '+now,'---','','# Inbox 整理状态','','状态只按程序整理回执、内容 SHA256、转换映射和队列快照推导；原件不改。登记页不计作已萃取。','',f'文件总数：{len(inventory)}；仍无匹配回执：{len(unmatched)}。','']
 for label,rows in groups.items():
  lines += [f'## {label}（{len(rows)}）','']
  for row in rows:
   line=f"- [[{row['path']}|{pathlib.PurePosixPath(row['path']).name}]]；整理日期：{row['date']}"
   if row['receipt']:line+='；回执：[['+row['receipt']+']]'
   if row['wiki']:line+='；Wiki：'+'、'.join('[['+x+']]' for x in row['wiki'])
   if row['reason']:line+='；'+row['reason']
   lines.append(line)
  lines.append('')
 lines += ['## 仍无匹配回执（按原因）','']+[f'- {k}：{v}' for k,v in sorted(roots.items())]+['']+['- [['+p+']]' for p in unmatched]+['']
 dependencies=set(inventory)|{row['receipt'] for rows in groups.values() for row in rows if row['receipt']}|{x['via'] for x in resolved}
 return {'body':'\n'.join(lines),'counts':{k:len(v) for k,v in groups.items()},'total':len(inventory),'unmatched':unmatched,'roots':dict(roots),'resolved_via_derived':resolved,'generated':now,'_read_hashes':{p:hashlib.sha256(t.encode()).hexdigest() for p,t in cache.items() if p in dependencies and t is not None},'_receipt_cache':{p:{'stamp':receipt_stamps[p],'body':t} for p,t in cache.items() if p in receipt_stamps and t is not None}}

def run(root,publish=True,preview=False):
 spec=importlib.util.spec_from_file_location('vc',root/'vault-client.py');vc=importlib.util.module_from_spec(spec);spec.loader.exec_module(vc)
 cfg=json.loads((root/'credentials.json').read_text());c=vc.MCP(os.environ.get('WIKI_MCP_URL','https://wiki.example.invalid/mcp'),cfg['mcp_token'],timeout=45)
 spec=importlib.util.spec_from_file_location('fw',root/'format/format-worker.py');fw=importlib.util.module_from_spec(spec);spec.loader.exec_module(fw)
 try:
  # Start the existing read-only Node bridge before limiting this Python process:
  # V8 reserves virtual address space larger than its real memory use.
  attachments=fw.bridge({'action':'list'}) if not preview else None
  import resource
  resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024))
  receipt_cache_path=root/'generated-refresh/inbox-receipt-cache.json'
  previous_receipts=json.loads(receipt_cache_path.read_text()) if receipt_cache_path.exists() else {}
  result=json.loads((root/'generated-refresh/inbox-status-preview.json').read_text()) if preview else compute(c,lambda request:attachments,json.loads((root/'format/state.json').read_text()),previous_receipts)
  if '_receipt_cache' in result:
   receipt_cache_path.write_text(json.dumps(result.pop('_receipt_cache'),ensure_ascii=False));receipt_cache_path.chmod(0o600)
 finally:fw.close_bridge()
 state=root/'generated-refresh';state.mkdir(mode=0o700,exist_ok=True)
 preview=state/'inbox-status-preview.json';preview.write_text(json.dumps(result,ensure_ascii=False,indent=2));preview.chmod(0o600)
 if not publish:return result
 lease=json.loads(c.tool('curation_writer_acquire',owner='每日整理后刷新Inbox状态'))
 if lease.get('status')!='ACQUIRED':return {'status':'BUSY','changed':False}
 try:
  for p,digest in result.pop('_read_hashes').items():
   current=body(c,p)
   if current is None or hashlib.sha256(current.encode()).hexdigest()!=digest:raise RuntimeError('Source changed before status publication: '+p)
  backups=state/'inbox-status-backups';backups.mkdir(mode=0o700,exist_ok=True)
  bodies={TARGET:result['body'],OLD:'---\nkind: generated_redirect\nexclude_from_ingest: true\nexclude_from_query: true\n---\n\n# 未整理资料\n\n唯一状态页：[[00-Inbox/整理状态.md|Inbox 整理状态]]。本页不再单独统计。\n'}
  before={p:body(c,p) for p in bodies};dest=backups/(datetime.datetime.now().strftime('%Y%m%dT%H%M%S%f')+'.json');dest.write_text(json.dumps(before,ensure_ascii=False));dest.chmod(0o600)
  for p,t in bodies.items():
   if before[p]!=t:
    answer=c.tool('write_note',path=p,content=t)
    if 'saved' not in answer.lower():raise RuntimeError('Status write rejected: '+answer[:200])
   if body(c,p)!=t:raise RuntimeError('Status readback mismatch')
  result.update(status='PASS',backup=str(dest));result.pop('body')
  tmp=state/'inbox-status-last.tmp';tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2));tmp.chmod(0o600);tmp.replace(state/'inbox-status-last.json')
  return result
 finally:c.tool('curation_writer_release',token=lease['token'])
