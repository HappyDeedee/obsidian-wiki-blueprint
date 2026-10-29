"""Source-bound person views. No voice/department guessing and no model call.

Seed syntheses are reviewed maintenance drafts, never owner confirmations.
New meetings append dated evidence; a daily pass recompiles the upper draft.
"""
import datetime,json,pathlib,re,copy,time
import yaml

SEEDS=pathlib.Path('/var/lib/kb-confirm/people-v2-seeds.json')

def profile_question(name,seed):
 return '主人请确认：依据已查的招投标/AI项目本人发言，将'+name+'与主人的工作关系及影响力概括为“'+seed['relationship']['text']+'”，准确吗？若不对请更正；此题不确认姓名，也不推定上下级。'

def segments(text):
 out=[]
 for m in re.finditer(r'\*\*([^\n]*?) · 发言人 (\d+)(?: · [^\n]*)?\*\*\s*\n(.*?)(?=\n\*\*|\Z)',text,re.S):
  anchor=re.search(r'(?m)^\^sentence-(\d+)\s*$',m[3])
  if anchor:out.append({'speaker':m[2],'time':m[1],'anchor':'sentence-'+anchor[1],'text':m[3][:anchor.start()].strip()})
 return out

def select_own(bundles):
 out=[]
 for mid,b in bundles.items():
  own=[s for s in segments(b['text']) if s['speaker'] in b['speakers']]
  if not own:raise ValueError('No confirmed speaker blocks '+mid)
  out.extend({**s,'meeting':mid,'path':b['transcript'],'title':b['title']} for s in own)
 return out

def date(title):
 m=re.match(r'(\d{2})-(\d{2})-(\d{2})',title)
 return '20'+'-'.join(m.groups()) if m else None

def render(name,existing,bundles,identities,curated=None,now=None,*,mode='append',seed=None):
 now=now or datetime.date.today().isoformat()
 fm=yaml.safe_load(existing.split('---',2)[1]) if existing.startswith('---') else {}
 seed=seed if seed is not None else json.loads(SEEDS.read_text()).get(name,{})
 seed=copy.deepcopy(seed)
 confirmed_claims=[]
 for card_path in pathlib.Path('/var/lib/kb-confirm/items').glob('q-*.json'):
  card=json.loads(card_path.read_text());payload=card.get('payload',{})
  if payload.get('candidate_kind')!='person-profile-v2' or payload.get('person')!=name:continue
  answer=card.get('answer') or {};change=card.get('change') or {}
  if card.get('status')!='resolved' or change.get('write')=='pending' and time.time()<change.get('due',0):continue
  claim=seed.get(payload.get('claim_key'),{})
  if not claim:continue
  if answer.get('kind')=='confirm':claim['inferred']=False;confirmed_claims.append(card['id'])
  elif answer.get('kind')=='fix' and answer.get('text'):
   claim.update(text=answer['text'],inferred=False);confirmed_claims.append(card['id'])
  elif answer.get('kind')=='invalid':claim.update(text='主人否定原画像判断；具体关系暂无。',inferred=False);confirmed_claims.append(card['id'])
 own=select_own(bundles) if bundles else []
 by={(s['meeting'],s['anchor']):s for s in own}
 def ev(c):return by.get((c.get('meeting'),c.get('anchor')))
 def link(s,label='原话'):return '[['+s['path']+'#^'+s['anchor']+'|'+label+']]'
 refs=[]
 def line(c):
  s=ev(c)
  if not s:return None
  quote=re.sub(r'\s+',' ',s['text'])[:28]
  ref=len(refs)+1;refs.append(f'[{ref}] “{quote}'+('…' if len(s['text'])>28 else '')+'” '+link(s))
  return c['text']+f' [{ref}]'+(' ^[inferred]' if c.get('inferred') else '')
 def section(label,c):
  t=line(c) if c else None
  return '- **'+label+'：**'+(t or '暂无可靠依据。')+'\n'
 roles=sorted({x['role'] for x in identities if x.get('role') and x['role'] not in ['未知','暂无']})
 dates=sorted(filter(None,(date(b['title']) for b in bundles.values())))
 olddeps=set(fm.get('profile_identity_dependencies',[]));validdeps={x['voice']+'|'+x['card'] for x in identities}
 deps_changed=bool(olddeps-validdeps or set(fm.get('profile_source_meetings',[]))-set(bundles))
 confirmed=fm.get('profile_owner_confirmed') is True and not deps_changed
 fm.update(title=name,type='人物',updated=now,rule_version='person-v2.1',
  role='；'.join(roles) if roles else None,org=fm.get('org'),
  status='主人已确认' if confirmed else '待主人确认草稿' if bundles else '待核',
  first_seen=dates[0] if dates else None,last_interaction=dates[-1] if dates else None,meeting_count=len(bundles),
  aliases=fm.get('aliases',[]),related_projects=sorted({p['path'] for p in seed.get('project_links',[]) if p['path'].startswith('30-Projects/') and set(p.get('meetings',[])) & set(bundles)}),
  related_projects_basis='已确认参会来源与项目页 sources 的交集，仅表示相关，不表示任职或项目负责人',
  date_basis='会议文件夹日期默认作为会期；仅有明确反证时调整',
  fact_state_mapping={'extracted':'unverified（只证明发言）','inferred':'hypothesis','ambiguous':'unverified','commitment':'planned'},
  profile_owner_confirmed=confirmed,profile_identity_dependencies=sorted(validdeps),
  profile_confirmed_claim_cards=confirmed_claims,
  profile_source_meetings=sorted(bundles),sources=['[['+b['transcript']+']]' for b in sorted(bundles.values(),key=lambda b:b['title'])])
 fm.setdefault('created',now)
 domains=sorted({x['domain'] for x in identities})
 if len(domains)==1:fm['domain']=domains[0]
 relation=seed.get('relationship',{})
 fm['relationship']=relation.get('text') if ev(relation) else None
 upper='## 当前画像\n\n'
 one=line(seed.get('one',{}));upper+='**一句话：**'+(one or '暂无已确认身份支持的画像。')+'\n\n'
 upper+='- **角色与职责：**'+('主人确认角色“'+fm['role']+'”。' if roles else '正式岗位暂无主人确认。')+'单位：'+(fm.get('org') or '暂无')+'。\n'
 upper+=section('关系与影响力',relation)
 upper+='- **关注点：**\n'
 for c in seed.get('concerns',[])[:5]:
  t=line(c)
  if t:upper+='  - '+t+'\n'
 upper+=section('沟通建议',seed.get('style'))+section('项目立场',seed.get('stance'))+section('关系网',seed.get('network'))
 new=set(bundles)-set(seed.get('reviewed_meetings',[]))
 if new:
  upper+='- **新证据待归纳：**'+str(len(new))+' 场新会议已入时间线；不把未审阅的新内容直接升为画像结论。\n'
 upper+='\n<details><summary>代表原话与出处（展开）</summary>\n\n'+'\n\n'.join(refs)+'\n\n</details>\n\n'
 # An append-only meeting update cannot silently rewrite a reviewed portrait.
 if (mode=='append' or confirmed) and not deps_changed and '## 当前画像\n' in existing and '## 证据时间线\n' in existing:
  upper='## 当前画像\n'+existing.split('## 当前画像\n',1)[1].split('## 证据时间线\n',1)[0]
 rows=[]
 for mid,b in sorted(bundles.items(),key=lambda x:x[1]['title'],reverse=True):
  choices=[s for s in own if s['meeting']==mid and 8<=len(s['text'])<=450]
  candidates=[c for key in ['one','concerns','stance','facts'] for c in (seed.get(key,[]) if isinstance(seed.get(key,[]),list) else [seed[key]]) if isinstance(c,dict) and c.get('meeting')==mid and ev(c)]
  c=candidates[0] if candidates else None
  s=ev(c) if c else max(choices,key=lambda s:sum(w in s['text'] for w in ['需求','问题','流程','使用','数据','质检','排版']),default=None)
  if not s:continue
  event=c['text'] if c else '本人表态：“'+re.sub(r'\s+',' ',s['text'])[:100]+'”'
  rows.append(f"- {date(b['title']) or '会期未知'} | [[{b['summary']}|{b['title'][9:]}]] | {event} | {link(s)} | "+('推断' if c and c.get('inferred') else '原话'))
 lower='## 证据时间线\n\n日期 | 会议 | 事件或表态 | 原话链接 | 置信度\n\n'+'\n'.join(rows)+'\n\n'
 if fm.get('rule_version')=='person-v2.1' and '### 承诺与待办\n' in existing and not deps_changed:
  oldlower=existing.split('## 证据时间线\n',1)[1] if '## 证据时间线\n' in existing else ''
  if oldlower and mode!='initial':
   # Keep old events; append only genuinely new meeting lines.
   kept=oldlower.split('### 承诺与待办\n',1)[0]
   additions=[row for row in rows if not any(b['summary'] in kept for mid,b in bundles.items() if b['summary'] in row)]
   lower='## 证据时间线\n'+kept+('\n### 新增会议\n\n'+'\n'.join(additions)+'\n\n' if additions else '')
 lower+='### 承诺与待办\n\n'
 promises=[]
 for c in seed.get('promises',[]):
  s=ev(c)
  if s:promises.append('- '+c['text']+' `fact_state: planned`。'+link(s))
 lower+='\n'.join(promises) if promises else '- 暂无可归给本人的明确承诺、截止和完成回执。'
 lower+='\n- 主人对本人的承诺：暂无可据原话和回执确认的条目。\n\n### 事实与数字\n\n'
 facts=[]
 for c in seed.get('facts',[]):
  s=ev(c)
  if s:facts.append('- '+(date(s['title']) or '日期未知')+'｜'+c['text']+' `fact_state: unverified`。'+link(s))
 lower+='\n'.join(facts) if facts else '暂无已逐项核对、适合单列的数字；不从转写片段猜数值。'
 lower+='\n\n### 矛盾与未决\n\n'
 issues=[]
 for c in seed.get('conflicts',[]):
  s=ev(c)
  if s:issues.append('- '+c['text']+' ^[ambiguous] '+link(s))
 lower+='\n'.join(issues) if issues else '暂无已核对的冲突；岗位与汇报关系未确认的内容留在画像草稿。'
 lower+='\n'
 if deps_changed:lower+='- 身份依据已撤销或更改，旧画像已备份；当前只按仍有效的已确认发言归属重建。\n'
 if 'rule_version: person-v2.1' in existing and '## 证据时间线\n' in existing and not deps_changed and mode!='initial':
  oldlower=existing.split('## 证据时间线\n',1)[1]
  additions=[row for row in rows if not any(b['summary'] in oldlower for b in bundles.values() if b['summary'] in row)]
  if additions:
   added='### 新增会议\n\n'+'\n'.join(additions)+'\n\n'
   oldlower=oldlower.replace('### 承诺与待办\n',added+'### 承诺与待办\n',1)
  lower='## 证据时间线\n'+oldlower
  # Preserve existing evidence and owner edits; merge newly reviewed promises,
  # facts and conflicts instead of silently losing them during daily compilation.
  additions={
   '承诺与待办':promises,
   '事实与数字':facts,
   '矛盾与未决':issues,
  }
  for heading,entries in additions.items():
   marker='### '+heading+'\n'
   if marker not in lower:continue
   start=lower.index(marker)+len(marker)
   end=lower.find('\n### ',start)
   if end<0:end=len(lower)
   missing=[entry for entry in entries if entry not in lower[start:end]]
   if missing:lower=lower[:end].rstrip()+'\n'+'\n'.join(missing)+'\n\n'+lower[end:]
 lower=re.sub(r'\n### 画像待确认\n.*\Z','',lower,flags=re.S)
 if bundles and not confirmed_claims and ev(seed.get('relationship',{})):
  lower+='\n### 画像待确认\n\n- '+profile_question(name,seed)+'\n'
 return '---\n'+yaml.safe_dump(fm,allow_unicode=True,sort_keys=False).strip()+'\n---\n\n# '+name+'\n\n'+upper+lower
