#!/usr/bin/env python3
"""确认中心：本机与可选远端入口同步（纯程序，无 AI）。
顺序固定：先拉取网页上的确认/撤销 -> 在本机按原逻辑应用（写 voices.json、记旧值、记日志）-> 重算声纹预测 -> 推送卡片。
本机队列 /var/lib/kb-confirm/items 是唯一正本；云端只是网页入口。"""
import json, os, sys, time, hashlib, base64, subprocess, urllib.request
from pathlib import Path
sys.path.insert(0, '/opt/kb-confirm')
import server  # 复用 resolve / undo（同一把文件锁，同一份日志）

R = Path('/var/lib/kb-confirm'); CFG = json.loads((R / 'cloud.json').read_text())
STATE = R / 'cloud_state.json'
st = json.loads(STATE.read_text()) if STATE.exists() else {'items': {}, 'files': {}, 'meta': ''}
VIA = '网页（远端确认入口）'

# 新环境默认 local：只跑答复写入与回执，不启用历史远端分支。
if CFG.get('mode') == 'local':
    import kb_qwrite
    n = kb_qwrite.run()
    if n: print(time.strftime('%F %T'), 'wrote', n)
    try:
        checked = subprocess.run([sys.executable, '/opt/kb-confirm/kb_feedback.py'], capture_output=True, text=True, timeout=45)
        if checked.returncode: raise RuntimeError(checked.stderr[-160:])
        print(time.strftime('%F %T'), 'ingestion receipts', checked.stdout.strip())
    except Exception as exc:
        print(time.strftime('%F %T'), 'ingestion receipt check failed', type(exc).__name__, str(exc)[:160])
    sys.exit(0)

def api(a, **body):
    body.update(a=a, s=CFG['sync_key'])
    req = urllib.request.Request(CFG['api'], data=json.dumps(body, ensure_ascii=False).encode(), headers={'Content-Type': 'text/plain'})
    with urllib.request.urlopen(req, timeout=60) as r: return json.loads(r.read())

# 1) 拉取并应用
ev = api('pull').get('events', []); acked = []
for e in ev:
    f = R / 'items' / f"{e['id']}.json"
    if f.exists():
        it = json.loads(f.read_text())
        if e['op'] == 'resolve' and it['status'] == 'open': server.resolve(e['id'], e['answer'], VIA)
        elif e['op'] == 'undo' and it['status'] in ('resolved', 'snoozed'): server.undo(e['id'], VIA)
    acked.append(e['_id'])
if acked: api('ack', ids=acked)

# 1b) 待确认问题：过了撤销窗口的回答写入 00-Inbox/待确认答复/（标准答复格式）
try:
    import kb_qwrite; wrote = kb_qwrite.run()
except Exception as x:
    wrote = 0; print('qwrite error', str(x)[:200])

# 2) 重算预测（只在有变化时）
if acked or not (R / 'predict.json').exists():
    subprocess.run(['/opt/kb-voiceprint/.venv/bin/python', '/opt/kb-confirm/vp_predict.py'], stdout=subprocess.DEVNULL, timeout=120)

# 3) 推送
def fid(asset):
    p = R / 'assets' / asset; h = hashlib.sha256(p.read_bytes()).hexdigest()
    rec = st['files'].get(asset)
    if rec and rec['sha'] == h: return rec['fileID']
    r = api('upload', name=asset, sha=h, b64=base64.b64encode(p.read_bytes()).decode())
    st['files'][asset] = {'sha': h, 'fileID': r['fileID']}; return r['fileID']

items, seen = [], set()
for f in sorted((R / 'items').glob('*.json')):
    it = json.loads(f.read_text()); seen.add(it['id'])
    ch = it.get('change') or {}
    doc = {'id': it['id'], 'type': it.get('type'), 'status': it['status'], 'answer': it.get('answer'),
           'resolved_at': it.get('resolved_at'), 'resolved_via': it.get('resolved_via'), 'created': it.get('created'), 'batch': it.get('batch'),
           'write': ch.get('write'), 'write_due': ch.get('due'), 'written_at': ch.get('written_at'), 'withdrawn': len(it.get('history', [])),
           'card': {k: it.get(k) for k in ('question', 'evidence', 'target', 'draft', 'choices')} | {k: it['payload'].get(k) for k in ('summary', 'label', 'title', 'page', 'refs')},
           'audio': [fid(a) for a in it.get('assets', []) if (R / 'assets' / a).exists()]}
    h = hashlib.sha256(json.dumps(doc, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if st['items'].get(it['id']) != h: items.append((doc, h))
remove = [i for i in st['items'] if i not in seen]
pr = json.loads((R / 'predict.json').read_text()) if (R / 'predict.json').exists() else {'people': [], 'items': {}}
mh = hashlib.sha256(json.dumps(pr, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
for i in range(0, max(len(items), 1), 20):
    chunk = items[i:i + 20]
    body = {'items': [d for d, _ in chunk]}
    if i == 0:
        body['remove'] = remove
        if mh != st['meta']: body.update(people=pr['people'], predict=pr['items'])
    if not chunk and not remove and mh == st['meta']: break
    r = api('push', **body)
    skipped = set(r.get('skipped', []))
    for d, h in chunk:
        if d['id'] not in skipped: st['items'][d['id']] = h
    if i == 0:
        for x in remove: st['items'].pop(x, None)
        st['meta'] = mh
tmp = STATE.with_suffix('.tmp'); tmp.write_text(json.dumps(st, ensure_ascii=False)); os.replace(tmp, STATE)
if acked or items or wrote: print(time.strftime('%F %T'), 'applied', len(acked), 'pushed', len(items), 'wrote', wrote)
