"""Business-date candidates, never filesystem-age archiving. No write to project pages."""
import datetime,re,hashlib

def progress_records(path,text):
    """Only an explicit dated business section; curation/history dates do not qualify."""
    result=[]
    parts=re.split(r'(?m)^(#{1,3})\s+',text)
    for i in range(1,len(parts),2):
        block=parts[i+1];heading,_,body=block.partition('\n')
        m=re.match(r'(\d{4}-\d{2}-\d{2})(?:\s|$)',heading)
        if not m or re.search(r'历史|补记|修订|导入|迁移记录',heading):continue
        if not(re.search(r'进展|验收|核对|上线|完成|接线',heading)or path.endswith('/进展日志.md')):continue
        # Plans alone do not prove that an event happened.
        if not re.search(r'已|完成|核对|核验|部署|上线|新增|记录|现有|目前',body):continue
        try:date=datetime.date.fromisoformat(m[1])
        except ValueError:continue
        result.append({'date':date.isoformat(),'path':path,'heading':heading.strip(),'quote':body.strip()[:300],'source_sha256':hashlib.sha256(text.encode()).hexdigest()})
    return result

def evaluate(notes,today):
    if isinstance(today,str):today=datetime.date.fromisoformat(today)
    rows=[]
    for path,text in sorted(notes.items()):
        if not path.startswith('30-Projects/')or not re.search(r'^type:\s*项目\s*$',text,re.M)or not re.search(r'^status:\s*进行中\s*$',text,re.M):continue
        records=progress_records(path,text)
        for target in re.findall(r'\[\[([^\]|#]+)',text):
            target=target if target.endswith('.md')else target+'.md'
            if target in notes and ('进展日志' in target or target.startswith('00-Inbox/会议/')):records+=progress_records(target,notes[target])
        known=[r for r in records if datetime.date.fromisoformat(r['date'])<=today]
        latest=max(known,key=lambda r:r['date'])if known else None
        days=(today-datetime.date.fromisoformat(latest['date'])).days if latest else None
        rows.append({'page':path,'last_progress':latest['date']if latest else None,'days':days,'status':'归档候选'if days is not None and days>=30 else'近期有进展'if latest else'进展日期未知','evidence':latest,'automatic_archive':False,'coverage':'只识别有明确日期的业务记录；未读或未识别的进展保留未知，不以文件更新时间替代'})
    return rows

def question(row):
    if row['status']!='归档候选':return None
    q=f"本次读取的项目页及关联业务记录中，最近可核实进展为{row['last_progress']}，距今超过30天；未读或未整理的进展可能尚未反映。该项目继续推进、补充新进展，还是归档？"
    return {'page':row['page'],'question':q,'type':'需主人','classification':'业务日期候选；不自动归档','priority':3,'evidence':row['evidence']}

def report(rows,today):
    out=['---','kind: generated','---','# 项目进展复核候选','',f'检查日：{today}。覆盖项目页及其已读取的关联业务记录，不代表全库穷尽检索。只提示，不改变项目状态；日期未知不视为停滞。','','| 项目 | 本次可核实业务日期 | 距检查日 | 结果 | 依据 |','| --- | --- | --- | --- | --- |']
    for r in rows:
        e=r['evidence'];basis=('[[%s]]：%s'%(e['path'].removesuffix('.md'),e['heading']))if e else'没有识别到可靠业务日期'
        out.append('| [[%s]] | %s | %s | %s | %s |'%(r['page'].removesuffix('.md'),r['last_progress']or'未知',str(r['days'])+'天'if r['days']is not None else'未知',r['status'],basis.replace('|','&#124;')))
    return '\n'.join(out)+'\n'
