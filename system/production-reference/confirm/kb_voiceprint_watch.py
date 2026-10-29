#!/usr/bin/env python3
"""Private, serial meeting voice worker. No model route, no full-audio intermediates."""
import fcntl,hashlib,json,math,os,pathlib,shutil,subprocess,sys,time
from urllib.parse import quote
D=pathlib.Path('/srv/wiki/meeting/data');V=pathlib.Path('/var/lib/kb-voiceprint');R=pathlib.Path('/var/lib/kb-confirm')
MIN_SIM=.82;MIN_GAP=.12;MIN_CONS=.75
def load(p,default=None):
 try:return json.loads(p.read_text())
 except FileNotFoundError:return default
def put(p,v):
 p.parent.mkdir(parents=True,exist_ok=True);t=p.with_name(p.name+'.tmp');t.write_text(json.dumps(v,ensure_ascii=False,indent=1));os.chmod(t,0o600);t.replace(p)
def cos(a,b):
 return sum(x*y for x,y in zip(a,b))/(math.sqrt(sum(x*x for x in a)*sum(x*x for x in b))+1e-12)
def sentences(h,speaker):
 tr=load(D/(h+'.Transcription.json'))['Transcription'];out=[]
 for p in tr.get('Paragraphs',[]):
  if str(p.get('SpeakerId'))!=str(speaker):continue
  ss={}
  for w in p.get('Words',[]):
   q=ss.setdefault(w['SentenceId'],[w['Start'],w['End'],'']);q[0]=min(q[0],w['Start']);q[1]=max(q[1],w['End']);q[2]+=w['Text']
  out.extend((a/1000,b/1000,t)for a,b,t in ss.values())
 return out
def clip(h,start,end,path):
 import av,numpy as np
 audio=next(D/(h+e)for e in ('.m4a','.mp3','.wav')if(D/(h+e)).is_file());c=av.open(str(audio));st=c.streams.audio[0]
 c.seek(int(max(start-.5,0)/st.time_base),stream=st,backward=True);rs=av.AudioResampler(format='flt',layout='mono',rate=16000);parts=[];t0=None
 for fr in c.decode(st):
  t=float(fr.pts*st.time_base)
  if t0 is None:t0=t
  if t>end:break
  for f in rs.resample(fr):parts.append(f.to_ndarray().reshape(-1))
 c.close()
 if not parts:raise RuntimeError('empty_clip')
 x=np.concatenate(parts)[max(int((start-t0)*16000),0):int((end-t0)*16000)]
 if len(x)<8000:raise RuntimeError('short_clip')
 path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.tmp.m4a');oc=av.open(str(tmp),'w');out=oc.add_stream('aac',rate=16000);out.layout='mono'
 fr=av.AudioFrame.from_ndarray(x.reshape(1,-1).astype(np.float32),format='flt',layout='mono');fr.sample_rate=16000
 for p in out.encode(fr):oc.mux(p)
 for p in out.encode(None):oc.mux(p)
 oc.close();os.chmod(tmp,0o600);tmp.replace(path)
def decide(entry,people):
 scores=sorted([{'name':n,'score':max(cos(entry.get('embedding',[]),e)for e in es)}for n,es in people.items()],key=lambda x:-x['score']) if entry.get('embedding') else []
 good=entry.get('segments_used',0)>=3 and entry.get('talk_seconds',0)>=20 and entry.get('consistency',0)>=MIN_CONS
 match=scores[0]['name']if good and scores and scores[0]['score']>=MIN_SIM and(scores[0]['score']-(scores[1]['score']if len(scores)>1 else 0))>=MIN_GAP else None
 return match,scores[:3]
def main():
 V.mkdir(parents=True,exist_ok=True)
 with open(V/'watch.lock','w')as lock:
  try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:return
  if shutil.disk_usage(V).free<1024**3:raise RuntimeError('disk_reserve_less_than_1GiB')
  voices=load(V/'voices.json',{});embs={p.stem:load(p)for p in(V/'meetings').glob('*.json')};covered={};people={}
  for x in voices.values():
   for m in x.get('members',[]):
    key=(m['meeting'],str(m['speaker']));covered[key]=x
    e=embs.get(m['meeting'],{}).get('speakers',{}).get(str(m['speaker']),{})
    if x.get('status')in('name','self')and e.get('embedding'):
     people.setdefault('知识库主人（本人）'if x['status']=='self'else x['name'],[]).append(e['embedding'])
  oldcards={}
  for p in(R/'items').glob('spk-*.json'):
   x=load(p)
   for m in x.get('payload',{}).get('members',[]):oldcards[(m['meeting'],str(m['speaker']))]=x['id']
  auto={};report={};errors=[];processed=0
  for p in sorted(D.glob('*.obsidian.json')):
   d=load(p,{});h=p.name.split('.')[0];path=d.get('note_path','')
   if not d.get('saved'):continue
   if not path.startswith('00-Inbox/会议/'):
    report[h]={'state':'EXCLUDED_TEST_OR_OUTSIDE_MEETINGS','note_path':path};continue
   try:
    if h not in embs:
     if processed>=1:report[h]={'state':'QUEUED_SERIAL','note_path':path};continue
     if not(D/(h+'.Transcription.json')).exists():raise RuntimeError('missing_transcription')
     staging=V/'extract-tmp';staging.mkdir(exist_ok=True)
     subprocess.run(['/opt/kb-voiceprint/.venv/bin/python','/opt/kb-voiceprint/vp_extract.py',str(D),h,str(staging)],check=True,timeout=900)
     source=staging/(h+'.json');e=load(source);assert e['meeting']==h and e['speakers'];source.replace(V/'meetings'/(h+'.json'));embs[h]=e;processed+=1
    ids={};candidates={}
    for s,e in embs[h]['speakers'].items():
     if(h,s)in covered:
      ids[s]={'state':'OWNER_CONFIRMED'if covered[h,s].get('status')in('name','self')else'OWNER_REVIEWED','card':covered[h,s].get('card')};continue
     if(h,s)in oldcards:
      ids[s]={'state':'CARD','card':oldcards[h,s]};continue
     n,score=decide(e,people);candidates[s]=(n,score)
    # A same-meeting identity collision is not resolved by a greedy guess.
    fixed={('知识库主人（本人）'if v.get('status')=='self'else v.get('name'))for (job,_),v in covered.items()if job==h and v.get('status')in('name','self')}
    counts={n:sum(x[0]==n for x in candidates.values())+(n in fixed)for n,_ in candidates.values()if n}
    for s,(name,scores)in candidates.items():
     if name and counts[name]==1:
      auto.setdefault(h,{})[s]={'name':name,'score':round(scores[0]['score'],4),'method':'声纹自动匹配','enrollment':'owner_confirmed_only'};ids[s]={'state':'AUTO_MATCH','name':name};continue
     iid='spk-m-'+h[:16]+'-'+s;dest=R/'items'/(iid+'.json')
     ss=sentences(h,s);usable=[x for x in ss if x[1]-x[0]>=1.0 and x[2].strip()]
     if not usable:raise RuntimeError('no_audition_sentence_for_'+s)
     a,b,t=max(usable,key=lambda x:(2<=x[1]-x[0]<=8,min(len(x[2]),100)))
     asset='voices/'+iid+'.m4a'
     if not(R/'assets'/asset).exists():clip(h,a,min(b,a+7),R/'assets'/asset)
     member={'meeting':h,'speaker':s,'title':path.rsplit('/',1)[-1].removesuffix('.md'),'talk_min':round(embs[h]['speakers'][s].get('talk_seconds',0)/60,2),'sample':t}
     it={'id':iid,'type':'speaker_identity','status':'open','created':time.strftime('%Y-%m-%d %H:%M:%S'),'question':member['title']+'：发言人'+s+'是谁？','payload':{'key':h+'#'+s,'voice':h+'#'+s,'members':[member],'summary':'待主人听音确认；程序相似度仅供参考，不代表身份已确认。','scores':scores},'assets':[asset],'evidence':[{'text':t,'source':f'{a:.2f}–{b:.2f}秒 · 发言人{s}','link':'obsidian://open?vault=MyWiki&file='+quote(d.get('transcript_path')or path)}],'target':'确认私有声音身份；kb-minutes 写回，人物页在批次边界走租约更新。'}
     with open(R/'.lock','w')as lf:
      fcntl.flock(lf,fcntl.LOCK_EX)
      if not dest.exists():put(dest,it)
     ids[s]={'state':'CARD','card':iid}
    report[h]={'state':'COMPLETE','note_path':path,'speakers':ids}
   except Exception as exc:
    errors.append({'meeting':h,'error':type(exc).__name__+': '+str(exc)[:180]});report[h]={'state':'ERROR','note_path':path,'error':errors[-1]['error']}
  anchor=hashlib.sha256((V/'voices.json').read_bytes()).hexdigest()
  automap={'voices_sha256':anchor,'matches':auto,'threshold':MIN_SIM,'gap':MIN_GAP}
  previous=load(V/'auto-matches.json',{})
  if {k:v for k,v in previous.items()if k!='at'}!=automap:put(V/'auto-matches.json',dict(automap,at=time.time()))
  put(V/'watch-status.json',{'at':time.time(),'meetings':report,'errors':errors,'extracted_this_run':processed})
  # Reuse this serial worker; do not add another controller or model run.
  for script in ('kb_person_attendance.py','kb_voice_status_report.py'):
   try:subprocess.run(['/opt/kb-voiceprint/.venv/bin/python','/opt/kb-confirm/'+script],check=True,timeout=100)
   except Exception as exc:errors.append({'stage':script,'error':type(exc).__name__+': '+str(exc)[:150]})
  put(V/'watch-status.json',{'at':time.time(),'meetings':report,'errors':errors,'extracted_this_run':processed})
  print(json.dumps({'extracted':processed,'states':{k[:8]:v['state']for k,v in report.items()},'automatic_matches':sum(map(len,auto.values())),'errors':errors},ensure_ascii=False))
if __name__=='__main__':main()
