"""File-backed review questions. A question is not a fact; an answer is not a commit."""
import os
import argparse,datetime,hashlib,importlib.util,json,pathlib,re,unicodedata,urllib.parse

def digest(s):return hashlib.sha256(s.encode()).hexdigest()
def norm(s):return re.sub(r'\s+',' ',unicodedata.normalize('NFC',s)).strip()
def cell(s):return str(s).replace('&','&amp;').replace('|','&#124;').replace('\n','<br>')
def uncell(s):return s.strip().replace('<br>','\n').replace('&#124;','|').replace('&amp;','&')

def questions(path,text):
    # Do not extract examples inside Markdown code blocks.
    lines=[];fence=None
    for line in text.splitlines():
        match=re.match(r'^ {0,3}(`{3,}|~{3,})',line)
        if match:
            mark=match[1]
            if fence is None:fence=mark
            elif mark[0]==fence[0]and len(mark)>=len(fence):fence=None
            continue
        if fence or line.startswith('    ')or line.startswith('\t'):continue
        lines.append(line)
    section=False;items=[];current=''
    for line in lines:
        if path.startswith('20-Wiki/人物/') and re.match(r'^###\s+画像待确认\s*$',line):
            if section and current:items.append(current);current=''
            section=True;continue
        if section and path.startswith('20-Wiki/人物/') and re.match(r'^###\s+',line):
            if current:items.append(current);current=''
            section=False;continue
        if re.match(r'^#{1,2}\s+',line):
            if section and current:items.append(current);current=''
            section=bool(re.match(r'^##\s*(?:矛盾与)?待确认(?:\s|$|[：:（(])',line));continue
        if not section:continue
        value=line.strip()
        if not value:continue
        bullet=re.match(r'^(?:[-*+]\s+|\d+[.)、]\s+)(.*)',value)
        if bullet:
            if current:items.append(current)
            current=bullet[1]
        else:current=(current+' '+value).strip()
    if section and current:items.append(current)
    result=[]
    for q in items:
        q=norm(q)
        if q in ['无','暂无','无。','暂无。','无待确认项','暂无待确认项']or not q:continue
        key='Q-'+digest(path+'\n'+q)[:20];version=digest(q)[:16]
        # These are routing hints, explicitly subject to human/health review.
        kind='需原文'
        if re.search(r'官网|官方|最新版本|固件|PyPI|外部|公开文档',q):kind='需外部'
        if re.search(r'主人|是否实际|是否配备|本人|用户确认|个人|同一|归档',q):kind='需主人'
        result.append({'id':key,'version':version,'page':path,'question':q,'type':kind,'classification':'程序初筛，待体检复核','priority':3 if path.startswith('30-Projects/')and re.search(r'^status:\s*进行中\s*$',text,re.M)else 1})
    return result

def registry_read(body):
    if body is None:return {'schema':1,'questions':{}}
    m=re.search(r'```json\n(.*?)\n```',body,re.S)
    if not m:raise ValueError('Question registry is not a supported generated registry')
    value=json.loads(m[1])
    if value.get('schema')!=1 or not isinstance(value.get('questions'),dict):raise ValueError('Invalid registry')
    return value

def answers(body,registry,source):
    result=[];issues=[]
    for line in body.splitlines():
        if not line.startswith('| Q-'):continue
        cols=[uncell(x)for x in line.strip().strip('|').split('|')]
        if len(cols) not in (6,7):issues.append({'source':source,'reason':'table row does not have six or seven columns'});continue
        # The added 整理助手 draft column is a proposal, never an owner answer.
        if len(cols)==7:
            qid,version,_,_,_,external_draft,answer=cols
        else:
            qid,version,_,_,_,answer=cols;external_draft=''
        if not answer:continue
        q=registry['questions'].get(qid)
        if not q or version!=q['version']:
            issues.append({'source':source,'question_id':qid,'reason':'unknown question/version; retained in original table'});continue
        identity=digest(qid+'\n'+version+'\n'+norm(answer))
        payload={'kind':'confirmation-response','question_id':qid,'question_version':version,'question':q['question'],'answer':answer,'question_page':q['page'],'answer_source':source,'answer_source_sha256':digest(body),'option_meanings':('项目候选：建（须有domain）/不是项目；旧表A建立、B暂不、C不同事项' if q.get('candidate_kind')=='new-project' else 'A 补充事实；B 不记得，保留未知；C 问题不成立（需说明）'),'review_required':True}
        if external_draft:payload['external_definition_draft']=external_draft
        # provenance refers to the actual owner-editable table, not a self-declared fact.
        result.append((identity,payload))
    return result,issues

def build(notes,old_registry,week,today):
    registry=registry_read(old_registry);current={}
    sp=importlib.util.spec_from_file_location('project_freshness',pathlib.Path(__file__).with_name('wiki-project-freshness.py'));fresh=importlib.util.module_from_spec(sp);sp.loader.exec_module(fresh)
    progress=fresh.evaluate(notes,today)
    ps=importlib.util.spec_from_file_location('project_candidates',pathlib.Path(__file__).with_name('wiki-project-candidates.py'));projects=importlib.util.module_from_spec(ps);ps.loader.exec_module(projects)
    nominations=projects.discover(notes)
    owner_reviewed={
      '30-Projects/API中转站.md':('项目模式未选定，上游来源、模型、价格和稳定性未写。','三件商品没有价格，也没有上架、咨询、成交或售后记录。')
    }
    incoming={p:0 for p in notes if p.startswith(('20-Wiki/','30-Projects/'))}
    for refpath,body in notes.items():
      if not refpath.startswith(('20-Wiki/','30-Projects/')):continue
      for target in set(re.findall(r'\[\[([^\]|#]+)',body)):
        target=target if target.endswith('.md')else target+'.md'
        if target in incoming:incoming[target]+=1
    for p,t in sorted(notes.items()):
        if p.startswith(('20-Wiki/','30-Projects/')):
            for q in questions(p,t):
                if q['id']in current and any(current[q['id']][k]!=q[k]for k in ['page','question','version']):raise ValueError('Question ID collision')
                if q['question']in owner_reviewed.get(p,()):
                    q['type']='需主人';q['classification']='维护者已核任务清单/进展日志/项目说明，当前模式与交易事实仍需主人确认'
                q['reference_count']=incoming.get(p,0)
                if q['priority']<3 and q['reference_count']>=3:q['priority']=2
                current[q['id']]=q
    for row in progress:
        q=fresh.question(row)
        if q:
            q['id']='Q-'+digest(q['page']+'\n项目进展归档复核')[:20]
            q['version']=digest(q['question'])[:16]
            q['reference_count']=incoming.get(q['page'],0)
            # Same question identity, a new explicit business-date version.
            prior=registry['questions'].get(q['id'])
            if prior and prior['version']!=q['version']:
                prior.setdefault('previous_versions',[]).append({k:prior.get(k)for k in ['version','question','answer_proposals']})
                prior.update(version=q['version'],question=q['question'],answer_proposals=[])
            current[q['id']]=q
    for q in nominations:current[q['id']]=q
    for qid,q in current.items():
        old=registry['questions'].get(qid,{})
        if old and any(old.get(k)!=q[k]for k in ['version','page','question']):raise ValueError('Question registry identity changed')
        registry['questions'][qid]={**old,**q,'first_seen':old.get('first_seen',today),'last_seen':today,'state':old.get('state','open'),'present':True}
    for qid,q in registry['questions'].items():
        if qid not in current:q['present']=False # Missing text does not prove resolution.
    responses={};issues=[]
    for p,t in sorted(notes.items()):
        if re.fullmatch(r'90-Logs/待确认/(?:推送/)?\d{4}-第\d{2}周\.md',p):
            found,errors=answers(t,registry,p);issues+=errors
            for identity,payload in found:responses.setdefault(identity,payload)
        elif p.startswith('00-Inbox/待确认答复/'):
            block=re.search(r'```json\n(.*?)\n```',t,re.S)
            if not block:continue # Free text is retained for ingest's semantic review.
            try:
                reply=json.loads(block[1])
                # 整理助手 proposals never count as owner replies, even with an 'answer' field.
                fm=t.split('---',2)[1]if t.startswith('---')and t.count('---')>=2 else ''
                if re.search(r'^kind:\s*[\"\']?hark_draft',fm,re.M)or reply.get('kind')=='hark_draft'or reply.get('hark_draft')or reply.get('role')=='hark_draft':
                    issues.append({'source':p,'reason':'hark_draft is not an owner response'});continue
                if reply.get('kind')not in (None,'confirmation-response'):
                    issues.append({'source':p,'reason':'not a confirmation-response'});continue
                qid=reply.get('question_id');q=registry['questions'].get(qid)
                if not q or reply.get('question_version')!=q['version']:
                    issues.append({'source':p,'question_id':qid,'reason':'unknown question/version; original retained'});continue
                answer=reply.get('answer')
                if not isinstance(answer,str)or not norm(answer):continue
                identity=digest(qid+'\n'+q['version']+'\n'+norm(answer))
                # A Bot's append-only reply and a table answer share one identity.
                # Receipt is not acceptance; this only prevents repeated weekly asking.
                q['answer_proposals']=sorted(set(q.get('answer_proposals',[])+[identity]))
            except (ValueError,TypeError,AttributeError):
                issues.append({'source':p,'reason':'invalid reply JSON; original retained'})
    # A received reply is awaiting review, never automatically a resolved fact.
    for identity,payload in responses.items():
        q=registry['questions'][payload['question_id']]
        q['answer_proposals']=sorted(set(q.get('answer_proposals',[])+[identity]))
    ordered=sorted(current.values(),key=lambda q:(-q['priority'],-q.get('reference_count',0),q['id']))
    candidates=[q for q in ordered if q['type']=='需主人'and registry['questions'][q['id']]['state']=='open'and not registry['questions'][q['id']].get('answer_proposals')][:5]
    batch='90-Logs/待确认/'+week+'.md'
    body=['---','kind: generated','---','# 本周待你确认','','每题可直接填事实，也可选 B（不记得，保留未知）或 C（问题不成立，请说明）。A 表示愿意补充，须同时写明事实。选项本身不证明任何业务结论。旧批次不覆盖，晚同步的答案下次仍会读取。',
          '', '项目候选可直接答“Q-xxxx：建，归<domain>”或“Q-xxxx：不是项目”；也可说“建项目：<名称>，归<domain>”。domain 仅用工作、创业、个人。仅提工具产品名称的旧误报不占每周最多5题名额。',
          '', '外部释义草案由整理助手填写，主人只作确认。草案不是主人答复，未经主人确认不写正式术语表、不用于检索展开。',
          '', '| 编号 | 问题版本 | 页面 | 问题 | 选项 | 外部释义草案（整理助手 填） | 你的回答 |','| --- | --- | --- | --- | --- | --- | --- |']
    for q in candidates:body.append('| '+' | '.join([q['id'],q['version'],'[['+q['page'].removesuffix('.md')+']]',cell(q['question']),'A 补充事实；B 不记得，保留未知；C 问题不成立（请说明）','',''])+' |')
    if not candidates:body+=['','本次没有初筛为“需主人”的问题；这不表示全库待确认已解决。']
    queue=['---','kind: generated','---','# 待确认队列','','分类为程序初筛，不能代替内容核查。问题从页面消失不自动记为已解决；矛盾保留双方依据。','','| 编号 | 页面 | 问题 | 类型 | 优先级 |','| --- | --- | --- | --- | --- |']
    for q in ordered:queue.append('| '+' | '.join([q['id'],'[['+q['page'].removesuffix('.md')+']]',cell(q['question']),q['type'],'进行中项目'if q['priority']==3 else'多页引用'if q['priority']==2 else'普通'])+' |')
    generated={'90-Logs/待确认/问题登记.md':'---\nkind: generated\n---\n# 问题登记\n\n程序维护问题登记与answer_proposals去重；出题统一由确认中心负责。旧批次及迟到答复保留，收到答复不表示事实已采纳。\n\n```json\n'+json.dumps(registry,ensure_ascii=False,indent=2)+'\n```\n','90-Logs/待确认队列.md':'\n'.join(queue)+'\n'}
    generated['90-Logs/知识保鲜/项目进展候选.md']=fresh.report(progress,today)
    generated['90-Logs/项目候选/当前候选.md']=projects.report(nominations,today)
    # Confirmation center alone issues new questions; immutable old batches are still read.
    delivery='90-Logs/待确认/推送/'+week+'.md'
    # Do not create a second weekly delivery path.
    for identity,payload in responses.items():
        path='00-Inbox/待确认答复/'+identity+'.md'
        if path not in notes:generated[path]='---\nkind: confirmation-response\n---\n# 待确认答复（待整理核对）\n\n```json\n'+json.dumps(payload,ensure_ascii=False,indent=2)+'\n```\n'
    return {'writes':generated,'count':len(current),'project_candidates':len(nominations),'selected':0,'question_issuer':'confirmation-center','answer_count':len(responses),'issues':issues,'batch':None}

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',default='/srv/wiki/stack');p.add_argument('--prefix',default='');p.add_argument('--fixture');p.add_argument('--publish',action='store_true');p.add_argument('--output');p.add_argument('--test-held-lease');p.add_argument('--held-lease');p.add_argument('--runtime-dir');a=p.parse_args()
    if a.prefix and not re.fullmatch(r'00-Test/[A-Za-z0-9_/-]+/',a.prefix):raise ValueError('Invalid isolated prefix')
    if a.fixture and not a.prefix:raise ValueError('Fixtures require an isolated prefix')
    if a.test_held_lease and not(a.fixture and a.prefix):raise ValueError('Held test lease only for isolated fixture rehearsal')
    root=pathlib.Path(a.root);s=importlib.util.spec_from_file_location('v',root/'vault-client.py');v=importlib.util.module_from_spec(s);s.loader.exec_module(v)
    vs=importlib.util.spec_from_file_location('views',pathlib.Path(__file__).with_name('wiki-generated-views.py'));views=importlib.util.module_from_spec(vs);vs.loader.exec_module(views)
    creds=json.loads((root/'credentials.json').read_text());c=v.MCP(os.environ.get('WIKI_MCP_URL','https://wiki.example.invalid/mcp'),creds['mcp_token'])
    if a.fixture:notes=json.loads(pathlib.Path(a.fixture).read_text())
    else:
        listing=c.tool('list_notes',limit=20000)
        if 'catching up'in listing or'may be incomplete'in listing:raise ValueError('Incomplete index')
        paths=sorted(set(urllib.parse.unquote(x).removesuffix('.md')+'.md'for x in re.findall(r'\(obsidian://open\?vault=[^&\n]+&file=([^\n]+)\)\s*$',listing,re.M)))
        notes={p:views.read(c,p)for p in paths if p.startswith(('20-Wiki/','30-Projects/','90-Logs/待确认/','00-Inbox/待确认答复/'))}
    for text in list(notes.values()):
        for target in re.findall(r'\[\[([^\]|#]+)',text):
            target=target if target.endswith('.md')else target+'.md'
            if target.startswith('10-Sources/')and target.endswith('/进展日志.md')and target not in notes:
                value=views.read(c,target)
                if value is not None:notes[target]=value
    key='90-Logs/待确认/问题登记.md';now=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)));iso=now.isocalendar();week=f'{iso.year}-第{iso.week:02}周'
    if a.fixture:
        # Prior generated state and answers are read from this isolated vault directory.
        for p in [key,'90-Logs/待确认/'+week+'.md']:
            actual=views.read(c,a.prefix+p)
            if actual is not None:notes[p]=actual
    result=build(notes,notes.get(key),week,now.date().isoformat());result['at']=now.isoformat()
    if a.publish:
        held=a.held_lease or a.test_held_lease
        lease=json.loads(pathlib.Path(held).read_text())if held else json.loads(c.tool('curation_writer_acquire',owner='待确认队列生成'))
        if lease['status']!='ACQUIRED':print(json.dumps({'status':'BUSY','changed':False}));return
        backup={};written=[]
        try:
            before=json.loads(c.tool('curation_status'))
            if not a.fixture:
                for p,t in notes.items():
                    if views.read(c,p)!=t:raise ValueError('Concurrent source or owner answer changed: '+p)
            for logical,body in result['writes'].items():
                path=a.prefix+logical;old=views.read(c,path)
                if old is not None and logical.startswith(('00-Inbox/','90-Logs/待确认/'+week)):
                    continue # append-only submissions / immutable week batches
                if logical==key and old!=notes.get(key):raise ValueError('Concurrent registry change')
                backup[path]=old
            b=(pathlib.Path(a.runtime_dir)if a.runtime_dir else root/'private')/'pending-queue-backups';b.mkdir(mode=0o700,exist_ok=True);bp=b/(now.strftime('%Y%m%dT%H%M%S%f')+'.json');bp.write_text(json.dumps(backup,ensure_ascii=False));bp.chmod(0o600)
            for logical,body in result['writes'].items():
                path=a.prefix+logical
                if path not in backup:continue
                if views.read(c,path)!=backup[path]:raise ValueError('Concurrent generated path changed')
                if backup[path]!=body:
                    answer=c.tool('write_note',path=path,content=body)
                    if not any(x in answer.lower()for x in ['saved','created']):raise ValueError('Write unconfirmed')
                    written.append(path)
                if views.read(c,path)!=body:raise ValueError('Server readback failed')
            after=json.loads(c.tool('curation_status'))
            if any(before[k]!=after[k]for k in ['next_index','successes','repairs','activity_counter']):raise ValueError('Unexpected queue/count change')
            result.update(status='PASS',written=written,backup=str(bp))
        except Exception:
            for path in reversed(written):
                old=backup[path]
                if old is not None:
                    logical=path.removeprefix(a.prefix)
                    if views.read(c,path)==result['writes'][logical]:
                        c.tool('write_note',path=path,content=old)
                        if views.read(c,path)!=old:raise ValueError('Rollback readback failed')
            raise
        finally:
            if not held:c.tool('curation_writer_release',token=lease['token'])
    if a.output:pathlib.Path(a.output).write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in result.items()if k!='writes'},ensure_ascii=False))
if __name__=='__main__':main()
