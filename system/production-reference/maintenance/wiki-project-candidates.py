"""Conservative project nominations from existing knowledge; never creates projects."""
import hashlib,re

def front(text):
    m=re.match(r'^---\s*\n(.*?)\n---(?:\n|$)',text,re.S)
    return m[1]if m else ''

def field(text,key):
    m=re.search(r'^'+re.escape(key)+r':\s*([^\n]*)',front(text),re.M)
    return m[1].strip().strip('\"\'')if m else ''

def names(path,text):
    result={path.rsplit('/',1)[-1].removesuffix('.md')}
    m=re.search(r'^aliases:\s*(.*?)(?=^\w[^\n]*:|\Z)',front(text),re.M|re.S)
    if m:
        value=m[1]
        result.update(x.strip().strip('\"\'')for x in re.findall(r'^\s*-\s*(.+)$',value,re.M))
        if value.strip().startswith('['):result.update(x.strip().strip('\"\'')for x in value.strip()[1:].split(']',1)[0].split(','))
    return {n for n in result if 2<=len(n)<=60}

def sources(text):
    m=re.search(r'^sources:\s*(.*?)(?=^\w[^\n]*:|\Z)',front(text),re.M|re.S)
    return [p if p.endswith('.md')else p+'.md'for p in re.findall(r'\[\[([^\]|#]+)',m[1]if m else '')]

def independent(path):
    # A meeting's transcript and minutes, or parts of one export, are one source.
    if path.startswith('00-Inbox/会议/'):return path.rsplit('/',1)[0]
    if path.startswith(('10-Sources/Codex/','10-Sources/Codex增量/')):
        m=re.search(r'(?:^|/)([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?=-\d+\.md|\.md|/)',path,re.I)
        if m:return 'codex:'+m[1].lower()
    return re.sub(r'(?:[-_/](?:part|分段|片段)[-_]?\d+)(?=\.|/|$)','',path,flags=re.I)

def roots(path,notes,seen=None):
    seen=set()if seen is None else seen
    if path in seen:return set()
    seen.add(path)
    if path.startswith(('20-Wiki/','30-Projects/')):
        return set().union(*(roots(p,notes,seen.copy())for p in sources(notes.get(path,''))))
    if path.startswith(('10-Sources/','00-Inbox/'))and not path.startswith(('00-Inbox/问答投稿/','00-Inbox/待确认答复/')):return {independent(path)}
    return set()

def discover(notes):
    existing=set().union(*(names(p,t)for p,t in notes.items()if p.startswith('30-Projects/')and field(t,'type')=='项目'))
    rows=[]
    for entity,text in sorted(notes.items()):
        if not entity.startswith('20-Wiki/')or field(text,'type')!='实体':continue
        aliases=names(entity,text)
        if aliases&existing:continue
        # A product/tool name does not itself name work owned by the user.
        # Prefer conservative omissions to making every frequently used tool a project.
        label=entity.rsplit('/',1)[-1].removesuffix('.md')
        if not re.search(r'运营|建设|改造|开发|迁移|推广|调研|招投标|招标|交付|优化|治理|经营',label):continue
        evidence=[];all_roots=set();action=False
        for path,body in sorted(notes.items()):
            # Aggregated entity pages cannot attribute a mention to every source.
            # Count only single-origin source summaries and actual body sentences.
            if not path.startswith('20-Wiki/来源摘要/')or field(body,'type')!='来源摘要':continue
            prose=re.sub(r'^---\s*\n.*?\n---(?:\n|$)','',body,flags=re.S)
            prose=re.sub(r'```.*?```','',prose,flags=re.S)
            lines=[line.strip()for line in prose.splitlines()if not line.lstrip().startswith('#')and not re.fullmatch(r'\s*[-*]?\s*\[\[.*?\]\]\s*',line)and any(n in line for n in aliases)]
            lines=[line for line in lines if not re.search(r'示例输出|改写范例|虚构|提示词范例|只被提及|已读完.*不更新',line)]
            if not lines:continue
            raw=roots(path,notes)
            if len(raw)!=1:continue
            acts=[line for line in lines if re.search(r'主人|用户|本人|我(?:们)?|知识库主人|书意',line)
                  and re.search(r'负责|推进|开发|交付|对接|调研|提交|上线|完成|制定|维护|运营|截止',line)]
            if not acts:continue
            action=action or bool(acts);all_roots|=raw
            evidence.append({'page':path,'sources':sorted(raw),'quote':(acts or lines)[0][:350],'sha256':hashlib.sha256(body.encode()).hexdigest()})
        if len(all_roots)<2 or not action:continue
        question=f'“{label}”在至少两份独立来源的已整理知识中出现，并识别到行动或截止候选。是否建立项目？A 建立（请确认 domain）；B 暂不建立；C 不是同一事项（请说明）。确认前仍需核对原始来源，不自动新建。'
        rows.append({'id':'Q-'+hashlib.sha256((entity+'\n项目建立候选').encode()).hexdigest()[:20],'version':hashlib.sha256(question.encode()).hexdigest()[:16],'page':entity,'question':question,'type':'需主人','classification':'至少两份独立来源均有主人行动/交付依据；工具产品名本身不构成项目','priority':2,'reference_count':0,'candidate_kind':'new-project','name':label,'independent_sources':sorted(all_roots),'evidence':evidence,'automatic_create':False,'policy':'owner-action-v2'})
    return rows

def report(rows,today):
    out=['---','kind: generated','---','# 项目建立候选','',f'检查日：{today}。只提有主人行动、交付或截止依据的工作事项；单纯工具、产品和平台名不列入。两份独立来源均须有明确主人行动，可能漏掉尚未整理或未写清主人的事项；没有候选不代表没有项目。项目原件不修改，不自动创建。',
         '', '确认方式：对任一 Bot 说“建项目：<名称>，归<domain>”，或“Q-xxxx：建，归<domain>”“Q-xxxx：不是项目”。domain 只用工作、创业、个人；未说明时先确认，不猜。答复保留主人原话和问题版本，沿每日整理采纳，重复答复不重复计数。',
         '', '| 编号 | 名称 | 独立来源数 | 行动/截止候选依据 |','| --- | --- | --- | --- |']
    for r in rows:
        evidence='；'.join('[['+e['page'].removesuffix('.md')+']]：'+e['quote'].replace('|','&#124;')for e in r['evidence'][:3])
        out.append('| '+r['id']+' | '+r['name']+' | '+str(len(r['independent_sources']))+' | '+evidence+' |')
    if not rows:out+=['','本次没有满足保守条件的候选；不代表库里没有其他项目。']
    return '\n'.join(out)+'\n'
