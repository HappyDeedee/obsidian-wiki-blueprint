#!/usr/bin/env python3
"""确认中心：每周从问题登记里挑出最值得问主人的几题，生成卡片（纯程序，无 AI）。

- 只读 90-Logs/待确认/问题登记.md（投稿身份，只读+只能新建答复）。
- 只挑：type=需主人、state=open、present=true、还没有任何答复（answer_proposals 为空）的问题。
- 10-25 起不限题数：全部开放的“需主人”问题按重要度排序全部出卡（噪音分≤0的不出）；“下周再说”的到新一周重新放出；
  同一题同一版本（question_id + question_version）只出一次卡，问题改版后按新版本重新出卡。
- 卡片只是提问；回答由 kb_qwrite.py 按库里既有的 confirmation-response 格式新建到 00-Inbox/待确认答复/，
  再由 wiki-pending-queue（不重复问）和 ingest（核对后更新知识）接手。
用法：make_question_cards.py [--dry]
"""
import os
import datetime, json, re, sys, importlib.util
from pathlib import Path
from urllib.parse import quote

R = Path('/var/lib/kb-confirm'); ITEMS = R / 'items'
WEEKLY = None  # 10-25 取消每周 5 题上限
sys.path.insert(0, '/opt/kb-confirm')
spec = importlib.util.spec_from_file_location('m', '/opt/kb-confirm/kb_minutes.py'); M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)
import server  # 同一把锁、同一份日志
from kb_register_read import read_register

TZ = datetime.timezone(datetime.timedelta(hours=8))
now = datetime.datetime.now(TZ); iso = now.isocalendar(); WEEK = f'{iso.year}-W{iso.week:02}'
WORK = re.compile('|'.join(re.escape(s) for s in json.loads(os.environ.get('WIKI_CARD_PRIORITY_TERMS', '[]'))) or r'(?!)')
NOISE = re.compile(r'时间戳|观察目录|PASS|FAIL|\br\d\d\b|content-unit|SVG|(?!)|calibration|observe|盲转录|长图单页|y=\d+|section-illustration|抠图|透明通道')
AGENT = re.compile(r'本轮未读|本次未读|未在本次读取|本轮没有读|未读取|不作判断|不能由链接名推出')
CHOICES = {'merge': ['同意按方案合并，旧页留重定向', '保留分开', '需要调整合并范围'], 'new-project': ['建项目，归工作', '建项目，归创业', '建项目，归个人', '暂不建', '不是同一件事'], 'person-domain': ['确认：工作', '改为：创业', '改为：个人', '先保留未知']}
LABEL = [('30-Projects/', '项目'), ('20-Wiki/实体/', '实体'), ('20-Wiki/综合/', '综合'), ('20-Wiki/概念/', '概念'), ('20-Wiki/来源摘要/', '来源')]

def label(p):
    return next((l for pre, l in LABEL if p.startswith(pre)), '其他')

def score(q):
    p, t = q.get('page') or '', q.get('question') or ''
    s = {'项目': 4, '实体': 3, '综合': 3, '概念': 1, '来源': 0}.get(label(p), 0)
    if WORK.search(p + t): s += 3
    s += min(int(q.get('reference_count') or 0), 50) / 25
    if q.get('priority') == 3: s += 2
    if NOISE.search(p + t): s -= 10
    if AGENT.search(t): s -= 10  # 10-25：只是“本轮未读取原文”的整理备注，该由程序读原文，不问主人
    if re.search(r'不再追问|已关闭|主人10-\d\d已答|保留原词|不改写|不要求主人', t): s -= 10
    if not re.search(r'是否|还是|哪|什么|谁|吗|[？?]|关系|未说明|未证|不明|待主人|归属|定位|用途|含义', t): s -= 10
    return s

def olink(p):
    return 'obsidian://open?vault=MyWiki&file=' + quote(p.removesuffix('.md'))

def vocabulary_precheck(dictionary, q):
    from kb_confirmed_dictionary import question_decision
    check = q.get('dictionary_check') or (q.get('draft') or {}).get('dictionary_check')
    if not check:
        text = (q.get('question') or '').strip().strip('？?。')
        match = re.fullmatch(r'[“「]?([^”」？?]{1,30})[”」]?(?:是什么(?:意思)?|的含义是什么|是什么意思|怎么写)', text)
        if match:check = {'term':match[1], 'intent':'definition'}
    return question_decision(dictionary, check) if check else {'action':'not_vocabulary_question'}


def main(dry=False):
    c = M.MCP(os.environ.get('WIKI_MCP_URL','https://wiki.example.invalid/mcp'), (R / 'answers_token').read_text().strip()); c.open()
    try:
        text = read_register(c)
    finally:
        c.close()
    reg = json.loads(re.search(r'```json\n(.*?)\n```', text, re.S)[1])['questions']

    from kb_dictionary_cache import read as read_dictionary
    from kb_confirmed_dictionary import close_card
    cached = read_dictionary()
    dictionary = cached['dictionary'] if cached else None
    decisions = {qid:vocabulary_precheck(dictionary,q) for qid,q in reg.items()} if dictionary else {}
    cards, asked = {}, set()
    for f in ITEMS.glob('q-*.json'):
        it = json.loads(f.read_text(encoding='utf-8'))
        prev = cards.get(it['payload']['qid'])
        if not prev or (it['status'] in ('open', 'snoozed', 'resolved') and prev['status'] == 'retired'): cards[it['payload']['qid']] = it
        asked.add((it['payload']['qid'], it['payload']['version']))
    auto_close = [it for qid,it in cards.items() if it['status'] in ('open','snoozed') and decisions.get(qid,{}).get('action')=='close']
    # 1) 已经有人从别处答过（周表/Bot）、问题改版或消失的开放卡：收起，不再占名额
    retire = []
    for qid, it in cards.items():
        q = reg.get(qid)
        if it['status'] in ('open', 'snoozed') and (not q or q.get('version') != it['payload']['version'] or q.get('state') != 'open'
                                                   or not q.get('present', True) or q.get('answer_proposals')):
            retire.append(it)
    # 2) “下周再说”的到新一周重新放出
    retire = [it for it in retire if it not in auto_close]
    wake = [it for it in cards.values() if it['status'] == 'snoozed' and it.get('snooze_week') != WEEK and it not in retire and it not in auto_close]
    live = [it for it in cards.values() if it['status'] == 'open' and it not in retire]
    pool = [dict(q, id=qid) for qid, q in reg.items()
            if q.get('type') == '需主人' and q.get('state') == 'open' and q.get('present', True)
            and not q.get('answer_proposals') and (qid, q.get('version')) not in asked
            and not (qid in cards and cards[qid]['status'] in ('open', 'snoozed', 'resolved') and cards[qid] not in retire)]
    pool.sort(key=lambda q: (-score(q), q['id']))
    pool = [q for q in pool if decisions.get(q['id'],{}).get('action')!='close']
    # A new term is asked once even if several pages minted different question IDs.
    from kb_confirmed_dictionary import normalized
    def semantic(q):
        check=q.get('dictionary_check')or(q.get('draft')or{}).get('dictionary_check')or q
        term=check.get('term')or check.get('canonical_term')
        if not term or check.get('intent')not in ('spelling','definition','alias_equivalence','confirmed_name_spelling'):return None
        # The same lexical item is one question, regardless of ID or wording.
        # Genuine conflicting senses stay separate; known entries were checked above.
        return (normalized(term),check.get('sense'))
    seen={semantic(reg.get(qid,{}))for qid,it in cards.items()if it['status']in ('open','snoozed')and it not in retire and it not in auto_close};seen.discard(None)
    unique=[]
    for q in pool:
        key=semantic(q)
        if key and key in seen:continue
        unique.append(q)
        if key:seen.add(key)
    pool=unique
    pick = [q for q in pool if score(q) > 0]
    out = {'week': WEEK, 'pool': len(pool), 'retire': [i['id'] for i in retire], 'wake': [i['id'] for i in wake],
           'new': [(q['id'], round(score(q), 1), q['page'], q['question'][:60]) for q in pick]}
    if dry:
        out['dictionary_auto_close'] = [it['id'] for it in auto_close]
        print(json.dumps(out, ensure_ascii=False, indent=1)); return

    @server.locked
    def commit():
        for it in auto_close:
            current = json.loads((ITEMS/(it['id']+'.json')).read_text(encoding='utf-8'))
            if current != it:continue
            updated = close_card(current, decisions[it['payload']['qid']], server.now())
            backup = R/'dictionary-card-backups'/f"{it['id']}-{now.strftime('%Y%m%dT%H%M%S')}.json"
            backup.parent.mkdir(exist_ok=True)
            if not backup.exists():backup.write_text(json.dumps(current,ensure_ascii=False),encoding='utf-8')
            server.save(updated);server.log({'t':server.now(),'op':'dictionary_auto_close','id':it['id'],'reason':updated['close_reason']})
        for it in retire:
            it.update(status='retired', retired_at=server.now()); server.save(it)
            server.log({'t': server.now(), 'op': 'retire', 'id': it['id'], 'why': '问题已在别处答复/改版/消失'})
        for it in wake:
            it.update(status='open', batch=WEEK, snooze_week=None); server.save(it)
            server.log({'t': server.now(), 'op': 'wake', 'id': it['id']})
        for q in pick:
            page = q['page']; title = page.rsplit('/', 1)[-1].removesuffix('.md')
            cid = 'q-' + q['id'][2:14]
            if (ITEMS / f'{cid}.json').exists(): cid += f"-v{q['version']}"
            it = {'id': cid, 'type': 'kb_question', 'status': 'open', 'created': server.now(), 'batch': WEEK,
                  'question': q['question'],
                  'payload': {'qid': q['id'], 'version': q['version'], 'page': page, 'label': label(page), 'title': title,
                              'summary': f"{label(page)} · {title}", 'score': round(score(q), 2),
                              'refs': int(q.get('reference_count') or 0)},
                  'evidence': [{'text': '这条待确认写在知识页的「待确认」小节里。', 'source': page, 'link': olink(page)}],
                  'draft': None, 'choices': CHOICES.get(q.get('candidate_kind')),
                  'target': '回答会按库里现有格式新建到 00-Inbox/待确认答复/，由整理程序核对原页后更新知识；不会直接改知识页。'}
            if q.get('candidate_kind') == 'merge':
                plan = q.get('merge_plan') or {}
                if not plan.get('target') or not plan.get('members') or not plan.get('preserve'):
                    raise ValueError('Incomplete merge proposal; keep report only')
                it['payload']['candidate_kind'] = 'merge'
                it['payload']['merge_plan'] = plan
                it['payload']['summary'] = '合并提案：' + title + '；旧页保留，差异逐条保留'
                it['draft'] = dict(q.get('draft') or {}, by='Codex/health 候选，未经主人确认')
                it['evidence'] = [{'text': x['quote'], 'source': x['page'], 'link': olink(x['page'])} for x in q.get('evidence', [])]
                it['evidence'] += [{'text': x, 'source': '合并后保留的差异'} for x in plan['preserve']]
                it['target'] = '只征求合并结构许可。主人同意后由现有串行整理核原文、拿租约、topic_repair；旧页留重定向及备份，不删除。'
            decision=decisions.get(q['id'],{})
            if decision.get('reason')=='conflict':
                it['question']+='\n与已确认词典条目 '+decision['confirmed_entry']+' 冲突；不是重新询问已知词。'
                it['evidence'] += [{'source':x['path'],'text':x.get('quote','已确认来源')}for x in decision['confirmed_sources']]
                it['evidence'].append({'source':decision['conflict']['source'],'text':decision['conflict']['quote']})
            server.save(it); server.log({'t': server.now(), 'op': 'card', 'id': it['id'], 'qid': q['id']})
    commit()
    print(json.dumps(out, ensure_ascii=False))

if __name__ == '__main__':
    main('--dry' in sys.argv)
