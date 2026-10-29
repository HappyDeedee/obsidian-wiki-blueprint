"""Render the actual weekly questions, without turning options into facts."""
import re,datetime
def section(body):
    rows=[]
    for line in body.splitlines():
        if not line.startswith('| Q-'):continue
        fields=[x.strip()for x in line.strip().strip('|').split('|')]
        if len(fields)!=6:raise ValueError('Unexpected confirmation table columns')
        qid,version,path,question,options,answer=fields
        if answer:continue
        question=question.replace('<br>',' ').replace('&#124;','|').replace('&amp;','&')
        rows.append(f'{qid}（{version}）：{question[:210]}')
    if not rows:return '本周待确认：本批没有待回答问题；不表示全库问题已经解决。'
    return '本周待你确认（程序初筛，最多5题）：\n'+'\n'.join(rows[:5])+'\n答法：编号＋补充事实；也可选 B“不记得，保留未知”，或 C“问题不成立＋理由”。A 需要同时给出事实。可直接在本周批次表填写。\n项目可答“Q-xxxx：建，归<domain>”“Q-xxxx：不是项目”，或“建项目：<名称>，归<domain>”。domain仅用工作、创业、个人；工具产品名本身的误报不占提问名额。'

def read_full(c,path):
    offset=0;parts=[]
    while True:
        raw=c.tool('read_note',path=path,offset=offset,limit=4000)
        if raw.startswith('Note not found:'):return None
        m=re.search(r'\[Segment offset=(\d+) next_offset=(END|\d+) total_chars=(\d+)\]\n',raw)
        if not m or int(m[1])!=offset:raise ValueError('Incomplete weekly question read')
        parts.append(raw[m.end():])
        if m[2]=='END':return ''.join(parts)
        offset=int(m[2])

def weekly(c,now):
    if now.weekday()!=0:return ''
    iso=now.isocalendar();path=f'90-Logs/待确认/{iso.year}-第{iso.week:02}周.md'
    try:
        selected='90-Logs/待确认/推送/'+path.rsplit('/',1)[1]
        body=read_full(c,selected)
        if body is None:
            selected=path
            body=read_full(c,path)
        if body is None:return '\n本周提问未完成：本周批次尚未生成，可能正在等待空闲租约；不会拿旧批次冒充。'
        return '\n'+section(body)+'\n本周表：'+selected
    except Exception as error:return '\n本周提问未完成：'+str(error)[:150]+'；日报照常发送。'

if __name__=='__main__':
    b='| Q-abc | v1 | [[x]] | 主人是否确认 | A/B/C | |\n| Q-def | v2 | [[y]] | 已回答 | A/B/C | B |'
    assert 'Q-abc'in section(b)and 'Q-def'not in section(b)
    class C:
        def tool(self,*a,**kw):raise RuntimeError('BUSY')
    assert '未完成'in weekly(C(),datetime.datetime(2026,10,19))
    assert weekly(C(),datetime.datetime(2026,10,20))==''
    print('PASS answered rows excluded, Monday only, read failure does not stop daily report')
