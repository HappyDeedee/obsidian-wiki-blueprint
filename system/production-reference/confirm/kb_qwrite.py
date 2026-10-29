#!/usr/bin/env python3
"""确认中心：把主人在网页上回答的待确认问题，按库里既有的 confirmation-response 格式新建到 00-Inbox/待确认答复/（纯程序，无 AI）。

- 回答后先等 10 分钟（可无痕撤销），再写入；写入用只能“新建答复”的投稿身份 kb-confirm-answers，不能改删任何笔记。
- 文件名与 wiki-pending-queue.py 的答复身份一致：sha256(question_id\\nversion\\n规范化答案)，同一回答不会写两次；
  写入后 wiki-pending-queue 会把该题记为“已收到答复”，不再每周重复问；ingest 按 question_id+version 核对原页后再更新知识。
- 答案字段只写主人的选择和原话；整理助手 草拟答案单独放在 hark_draft，标明不是主人答复。
"""
import os
import hashlib, json, re, time, unicodedata, importlib.util, sys
from pathlib import Path
sys.path.insert(0, '/opt/kb-confirm')
import server

R = Path('/var/lib/kb-confirm')
spec = importlib.util.spec_from_file_location('m', '/opt/kb-confirm/kb_minutes.py'); M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)
OPTION = 'A 补充事实；B 不记得，保留未知；C 问题不成立（需说明）'

def digest(s): return hashlib.sha256(s.encode()).hexdigest()
def norm(s): return re.sub(r'\s+', ' ', unicodedata.normalize('NFC', s)).strip()

def answer_text(it):
    a = it['answer']; k = a['kind']; t = (a.get('text') or '').strip(); d = (it.get('draft') or {}).get('text')
    if k == 'confirm': return 'A 对，认可 整理助手 草拟的答案：' + d + (('；补充：' + t) if t else '')
    if k == 'fix': return 'A ' + (('更正 整理助手 草拟答案：' if d else '') + t)
    if k == 'forgot': return 'B 不记得，保留未知' + (('。' + t) if t else '')
    if k == 'choose': return 'A 选择：' + t
    if k == 'invalid': return 'C 问题不成立' + (('：' + t) if t else '（主人点“不用问”，未写原因）')
    raise ValueError(k)

def build(it):
    p = it['payload']; ans = answer_text(it)
    rec = {'card': it['id'], 'answered_at': it['resolved_at'], 'via': it['resolved_via'], 'kind': it['answer']['kind'], 'answer': ans}
    payload = {'kind': 'confirmation-response', 'question_id': p['qid'], 'question_version': p['version'], 'question': it['question'],
               'answer': ans, 'question_page': p['page'],
               'answer_source': f"确认中心网页 · {it['resolved_at']} · 卡片 {it['id']}（主人本人点选/输入，经 kb-confirm-answers 程序写入，无 AI 改写）",
               'answer_source_sha256': digest(json.dumps(rec, ensure_ascii=False, sort_keys=True)),
               'option_meanings': OPTION, 'review_required': True,
               'owner_action': {'confirm': '认可草案', 'fix': '补充/更正', 'choose': '选择选项', 'forgot': '不记得', 'invalid': '问题不成立'}[it['answer']['kind']],
               'answered_at': it['resolved_at'] + ' +08:00'}
    dr = it.get('draft')
    if dr: payload['hark_draft'] = {'text': dr.get('text'), 'sources': dr.get('sources', []), 'note': '整理助手 草拟，不是主人答复；主人选“对”时才视为认可。'}
    prev = [h['file'] for h in it.get('history', []) if h.get('file')]
    if prev: payload['supersedes'] = prev[-1]; payload['supersedes_note'] = '主人在确认中心撤回了上一条答复，以本条为准。'
    ident = digest(p['qid'] + '\n' + p['version'] + '\n' + norm(ans))
    path = '00-Inbox/待确认答复/' + ident + '.md'
    body = '---\nkind: confirmation-response\n---\n# 待确认答复（待整理核对）\n\n```json\n' + json.dumps(payload, ensure_ascii=False, indent=2) + '\n```\n'
    return path, body

def run():
    due = []
    for f in sorted((R / 'items').glob('q-*.json')):
        it = json.loads(f.read_text(encoding='utf-8'))
        ch = it.get('change') or {}
        if it['status'] == 'resolved' and ch.get('write') == 'pending' and time.time() >= ch.get('due', 0): due.append(it['id'])
    if not due: return 0
    c = M.MCP(os.environ.get('WIKI_MCP_URL','https://wiki.example.invalid/mcp'), (R / 'answers_token').read_text().strip()); c.open(); n = 0
    try:
        for iid in due:
            @server.locked
            def one():
                it = server.load(iid); ch = it.get('change') or {}
                if it['status'] != 'resolved' or ch.get('write') != 'pending': return 0
                path, body = build(it)
                r = c.call('tools/call', {'name': 'write_note', 'arguments': {'path': path, 'content': body}})
                txt = '\n'.join(x.get('text', '') for x in r.get('content', []))
                if r.get('isError') and not re.search(r'exist', txt, re.I):
                    server.log({'t': server.now(), 'op': 'write_fail', 'id': iid, 'err': txt[:200]}); return 0
                it['change'] = {'write': 'done', 'file': path, 'written_at': server.now()}
                server.save(it); server.log({'t': server.now(), 'op': 'write', 'id': iid, 'file': path}); return 1
            n += one()
    finally:
        c.close()
    return n

if __name__ == '__main__':
    print(run())
