#!/usr/bin/env python3
"""One leased profile per existing worker cycle; revoke/rename rebuilds claims.

Only confirmation-card domain grants admit identities. No model or new worker.
"""
import os
import datetime,fcntl,hashlib,json,pathlib,re,signal,time
from kb_minutes import MCP
from kb_confirmed_dictionary import identity_map,from_markdown,PATH
from kb_dictionary_cache import refresh
from kb_person_profile import render
R=pathlib.Path('/var/lib/kb-confirm');D=pathlib.Path('/srv/wiki/meeting/data')
def load(p,d):
 try:return json.loads(p.read_text())
 except FileNotFoundError:return d
def put(p,v):
 t=p.with_suffix('.tmp');t.write_text(json.dumps(v,ensure_ascii=False));t.chmod(0o600);t.replace(p)
def sha(t):return hashlib.sha256(t.encode()).hexdigest()
def tool(c,n,**kw):return json.loads(c.tool(n,kw))

def read_existing(c,path):
 first=c.tool('read_note',{'path':path,'offset':0,'limit':1})
 if first.strip()=='Note not found: '+path:return None
 return c.read(path)

def confirmed_names(voices):
 """Name confirmation is sufficient for vocabulary; profile domain is separate."""
 out={}
 for vid,v in voices.items():
  if v.get('status')!='name':continue
  name=(v.get('name')or'').strip();card=v.get('card');at=v.get('confirmed_at')
  if not name or '/'in name or '\\'in name or name in {'.','..'}:raise ValueError('unsafe_person_name')
  if not card or not at:raise ValueError('manual_name_missing_confirmation_provenance')
  datetime.datetime.fromisoformat(at)
  out.setdefault(name,[]).append({'voice':vid,'card':card,'confirmed_at':at})
 return out
def run(mode="append"):
 signal.signal(signal.SIGALRM,lambda *_:(_ for _ in()).throw(TimeoutError('bounded person update')));signal.alarm(90)
 with (R/'people.lock').open('r+')as lock:
  try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:return
  voices=load(pathlib.Path('/var/lib/kb-voiceprint/voices.json'),{});grants=load(R/'people-domains.json',{})
  name_refs=confirmed_names(voices)
  if not grants:raise ValueError('Domain registry unavailable; preserve every page and dictionary entry')
  # A domain belongs to a confirmed voice/card, not to the spelling of its name.
  for vid,v in voices.items():
   g=grants.get(v.get('card'),{})
   if g.get('voice')==vid and v.get('status')=='name':g['confirmed_name']=v.get('name')
  current,blocked=identity_map(voices,grants)
  state=load(R/'people_merge_state.json',{'profiles':{},'identities':{}})
  names=sorted({x['name']for x in current.values()}|set(state['profiles']))
  desired={n:{k:v for k,v in current.items()if v['name']==n}for n in names}
  signatures={n:sha(json.dumps(v,ensure_ascii=False,sort_keys=True))for n,v in desired.items()}
  pending=[n for n in names if state['profiles'].get(n)!=signatures[n] or (mode=='daily' and state.get('portrait_days',{}).get(n)!=datetime.date.today().isoformat())]
  cfg=load(pathlib.Path('/srv/wiki/stack/credentials.json'),{})
  c=MCP(os.environ.get('WIKI_MCP_URL','https://wiki.example.invalid/mcp'),cfg['mcp_token']);c.open();lease=None
  try:
   if tool(c,'curation_status').get('writer'):return
   vocabulary=c.read(PATH);doc=from_markdown(vocabulary);refresh(vocabulary)
   # Refresh names from identity confirmation, without treating them as business facts.
   changed=False;now=datetime.datetime.now().astimezone().date().isoformat();active_names=set(name_refs)
   for e in doc['entries']:
    if e['kind']=='person'and e.get('managed_by_identity',True):
     active=e['canonical']in active_names
     if e.get('active',True)!=active:e['active']=active;changed=True
   for n in sorted(active_names):
    if any(e['kind']=='person'and e['canonical']==n and e.get('active',True)for e in doc['entries']):continue
    refs=name_refs[n]
    doc['entries'].append({'id':'person-'+sha(n)[:16],'canonical':n,'kind':'person','aliases':[],'typos':[],
      'definition':'确认中心人工确认的参会姓名；不据此推断岗位或业务事实。','confirmation':'owner_confirmed',
      'confirmed_date':min(x['confirmed_at'][:10]for x in refs),'date_basis':'确认中心人工确认日期，非会议日期','active':True,'managed_by_identity':True,
      'sources':[{'path':'confirmation-center://cards/'+x['card'],'quote':'确认中心现存人工身份记录：name='+n+'; voice='+x['voice'],
        'confirmation_cards':[x['card']],'voice':x['voice'],'confirmed_at':x.get('confirmed_at')}for x in refs[:1]]});changed=True
   if not pending and not changed:return
   lease=tool(c,'curation_writer_acquire',owner='人物页身份撤销改名与增量合并')
   if lease.get('status')!='ACQUIRED':lease=None;return
   backup=R/'people-backups';backup.mkdir(exist_ok=True)
   if changed:
    target=re.sub(r'(?ms)^```json\s*\n.*?\n```',lambda _: '```json\n'+json.dumps(doc,ensure_ascii=False,indent=2)+'\n```',vocabulary,count=1)
    assert c.read(PATH)==vocabulary
    (backup/('dictionary-'+sha(vocabulary)+'.md')).write_text(vocabulary)
    c.tool('write_note',{'path':PATH,'content':target});assert c.read(PATH)==target;refresh(target)
   for name in pending[:1]:
    records=list(desired[name].values());page='20-Wiki/人物/'+name+'.md'
    original=read_existing(c,page)
    before=original if original is not None else ('---\ntitle: '+name+'\ndomain: '+records[0]['domain']+'\ntype: 人物\nstatus: 进行中\n---\n'if records else None)
    if before is None:continue
    bundles={}
    for x in records:
     manifest=load(D/(x['meeting']+'.obsidian.json'),{})
     if not manifest.get('saved')or not str(manifest.get('transcript_path','')).startswith('00-Inbox/会议/'):raise ValueError('Meeting not ready')
     mid=x['meeting']
     if mid not in bundles:
      transcript=manifest['transcript_path'];summary=manifest['note_path'];text=c.read(transcript)
      bundles[mid]={'text':text,'speakers':[],'transcript':transcript,'summary':summary,'title':summary.split('/')[-2]}
     bundles[mid]['speakers'].append(x['speaker'])
    curated=load(R/'people-curated'/ (sha(name)+'.json'),{})
    after=render(name,before,bundles,records,curated,now=now,mode=mode)
    if not records:after=after.replace('status: 进行中','status: 待核')
    (backup/(sha(page)+'-'+sha(before)+'.md')).write_text(before)
    assert read_existing(c,page)==original,'Concurrent profile edit'
    assert tool(c,'curation_status').get('writer',{}).get('owner')==lease['owner']
    c.tool('write_note',{'path':page,'content':after});assert c.read(page)==after
    state['profiles'][name]=signatures[name]
    if mode=='daily':state.setdefault('portrait_days',{})[name]=now
   state.update(identities=current,blocked=blocked,pending=max(0,len(pending)-1),at=time.time());put(R/'people_merge_state.json',state)
  finally:
   if lease:tool(c,'curation_writer_release',token=lease['token'])
   c.close()
if __name__=='__main__':run()
