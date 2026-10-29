#!/usr/bin/env python3
"""KB 确认中心（v0.2 自托管：/kb/ 网页 + /kbc 接口；旧 v0.1 页面仍在 /old）。网页与 Bot 共用同一个文件队列：/var/lib/kb-confirm/items/*.json
- 只做固定程序的安全回写，每次回写记录旧值，可以撤销（log.jsonl）。
- 访问方式：首次打开 /k/<KEY> 会写入 cookie，之后直接打开 / 即可（适合添加到主屏幕）。
"""
import json, os, re, secrets, time, html, fcntl, mimetypes
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse, quote
from pathlib import Path

ROOT = Path(os.environ.get('KB_CONFIRM_ROOT', '/var/lib/kb-confirm'))
ITEMS, ASSETS, LOG = ROOT / 'items', ROOT / 'assets', ROOT / 'log.jsonl'
KEY = (ROOT / 'access.key').read_text().strip()
VOICES = Path('/var/lib/kb-voiceprint/voices.json')
LOCK = ROOT / '.lock'
PREDICT = ROOT / 'predict.json'
PY = '/opt/kb-voiceprint/.venv/bin/python'

def predictions():
    try: return json.loads(PREDICT.read_text(encoding='utf-8'))
    except Exception: return {'people': [], 'items': {}}

def repredict():
    import subprocess  # 纯程序声纹比对，后台跑，不阻塞网页
    subprocess.Popen(['systemd-run', '--quiet', '--collect', '--property=MemoryMax=300M', '--property=Nice=19',
                      PY, '/opt/kb-confirm/vp_predict.py'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def now(): return time.strftime('%Y-%m-%d %H:%M:%S')

def locked(fn):
    def w(*a, **k):
        with open(LOCK, 'w') as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            return fn(*a, **k)
    return w

def load(i): return json.loads((ITEMS / f'{i}.json').read_text(encoding='utf-8'))
def save(it):
    p = ITEMS / f"{it['id']}.json"; tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(it, ensure_ascii=False, indent=1), encoding='utf-8'); tmp.replace(p)
def items():
    out = [json.loads(p.read_text(encoding='utf-8')) for p in sorted(ITEMS.glob('*.json'))]
    return out
def log(rec):
    with open(LOG, 'a', encoding='utf-8') as f: f.write(json.dumps(rec, ensure_ascii=False) + '\n')

# ---------- 固定回写程序（按卡片类型） ----------
def apply_speaker(it, ans):
    voices = json.loads(VOICES.read_text(encoding='utf-8')) if VOICES.exists() else {}
    vid = it['payload'].get('key') or it['payload']['voice']
    old = voices.get(vid)
    new = {'status': ans['kind'], 'name': ans.get('name') or None, 'role': ans.get('role') or None, 'confirmed_at': now(),
           'card': it['id'], 'members': it['payload']['members']}
    voices[vid] = new
    tmp = VOICES.with_suffix('.tmp'); tmp.write_text(json.dumps(voices, ensure_ascii=False, indent=1), encoding='utf-8'); tmp.replace(VOICES)
    return {'file': str(VOICES), 'key': vid, 'old': old, 'new': new}

def undo_speaker(change):
    voices = json.loads(VOICES.read_text(encoding='utf-8')) if VOICES.exists() else {}
    if change['old'] is None: voices.pop(change['key'], None)
    else: voices[change['key']] = change['old']
    tmp = VOICES.with_suffix('.tmp'); tmp.write_text(json.dumps(voices, ensure_ascii=False, indent=1), encoding='utf-8'); tmp.replace(VOICES)

APPLY = {'speaker_identity': (apply_speaker, undo_speaker)}

Q_KINDS = ('confirm', 'fix', 'choose', 'forgot', 'invalid', 'later')
GRACE = 600  # 回答后 10 分钟内可无痕撤销；之后才写入知识库

def week_id():
    import datetime
    d = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isocalendar(); return f'{d[0]}-W{d[1]:02}'

@locked
def resolve(iid, ans, via):
    it = load(iid)
    if it['status'] != 'open':
        return it, False
    change = None
    if it['type'] == 'kb_question':
        if ans.get('kind') not in Q_KINDS: return it, False
        if ans['kind'] == 'later':
            it.update(status='snoozed', snooze_week=week_id(), answer=ans, resolved_via=via, resolved_at=now(), change=None)
            save(it); log({'t': now(), 'op': 'snooze', 'id': iid, 'via': via}); return it, True
        change = {'write': 'pending', 'due': time.time() + GRACE}
    elif ans['kind'] in ('name', 'self', 'unknown', 'mixed'):
        change = APPLY[it['type']][0](it, ans)
    it.update(status='resolved', answer=ans, resolved_via=via, resolved_at=now(), change=change)
    save(it); log({'t': now(), 'op': 'resolve', 'id': iid, 'via': via, 'answer': ans, 'change': change})
    return it, True

@locked
def undo(iid, via):
    it = load(iid)
    if it['status'] not in ('resolved', 'snoozed'): return it
    if it['type'] == 'kb_question':
        ch = it.get('change') or {}
        if ch.get('write') == 'done':
            # 已写入的答复只能新建、不能删；保留记录，重新回答会作为新答复交整理程序核对。
            it.setdefault('history', []).append({'answer': it.get('answer'), 'at': it.get('resolved_at'), 'file': ch.get('file'), 'withdrawn_at': now()})
        log({'t': now(), 'op': 'undo', 'id': iid, 'via': via, 'was': ch})
        it.update(status='open', answer=None, change=None, resolved_at=None, resolved_via=None, snooze_week=None)
        save(it); return it
    if it.get('change'): APPLY[it['type']][1](it['change'])
    log({'t': now(), 'op': 'undo', 'id': iid, 'via': via, 'reverted': it.get('change')})
    it.update(status='open', answer=None, change=None, resolved_at=None, resolved_via=None)
    save(it); return it

# ---------- 页面 ----------
CSS = """body{font:16px -apple-system,system-ui,'PingFang SC',sans-serif;margin:0;background:#f4f3ef;color:#1d1d1f}
header{position:sticky;top:0;background:#1d1d1f;color:#fff;padding:14px 16px;font-weight:600;z-index:2}
header small{font-weight:400;opacity:.7;margin-left:8px}
.card{background:#fff;margin:12px;border-radius:14px;padding:14px 16px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.q{font-weight:600;font-size:17px;margin-bottom:6px}.meta{color:#6e6e73;font-size:13px}
.ev{border-left:3px solid #d2d2d7;padding:2px 10px;margin:8px 0;font-size:14px}.ev a{color:#0a66c2;text-decoration:none;font-size:12px}
audio{width:100%;margin:8px 0}.pre{background:#f0f6ff;border-radius:8px;padding:8px 10px;font-size:14px;margin:8px 0}
input[type=text]{width:100%;box-sizing:border-box;font-size:17px;padding:10px;border:1px solid #c7c7cc;border-radius:10px}
.btns{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}button{font-size:15px;padding:10px 14px;border-radius:10px;border:0;background:#e8e8ed}
button.p{background:#0a66c2;color:#fff}.done{opacity:.6}.tag{display:inline-block;font-size:12px;padding:1px 6px;border-radius:6px;background:#eee;margin-left:4px}
nav{display:flex;gap:16px;padding:8px 16px;font-size:14px}nav a{color:#0a66c2;text-decoration:none}"""

def e(s): return html.escape(str(s or ''))

def card_html(it):
    p = it['payload']; pre = it.get('prefill') or {}
    ev = ''.join(f"<div class=ev>{e(x['text'])}<br><span class=meta>{e(x['source'])}</span> "
                 + (f"<a href='{e(x['link'])}'>打开原文</a>" if x.get('link') else '') + '</div>' for x in it['evidence'])
    audio = ''.join(f"<audio controls preload=metadata src='/a/{quote(a)}?k={KEY}'></audio>" for a in it.get('assets', []))
    target = f"<div class=meta>确认后会改：{e(it.get('target'))}</div>"
    if it['status'] == 'open':
        pr = predictions(); ip = pr['items'].get(it['id'], {}); sug = ip.get('suggest')
        guess = ''
        if ip.get('top'):
            t = '、'.join(f"{x['name']} {x['sim']:.2f}" for x in ip['top'])
            guess = (f"<div class=pre>声纹最像：<b>{e(sug)}</b><span class=tag>程序比对</span><br><span class=meta>相似度 {e(t)}</span></div>" if sug
                     else f"<div class=pre>声纹没有把握<br><span class=meta>相似度 {e(t)}（都不够高或太接近）</span></div>")
        elif pre.get('value'):
            guess = (f"<div class=pre>预填：<b>{e(pre.get('value'))}</b><span class=tag>{e(pre.get('confidence'))}</span><br>"
                     f"<span class=meta>依据：{e(pre.get('source'))}</span></div>")
        people = sorted(pr.get('people', []), key=lambda x: (x['name'] != sug, x['name']))
        chips = ''.join(f"<button type=submit name=pick value='{e(x['name'])}' class='{'p' if x['name']==sug else ''}'>{e(x['name'])}"
                        + (f"<small style='opacity:.7'> {e(x['role'])}</small>" if x.get('role') else '') + "</button>" for x in people)
        chips = f"<div class=meta style='margin-top:8px'>点名字直接确认：</div><div class=btns>{chips}</div>" if chips else ''
        form = (f"<form method=post action='/r/{it['id']}'><button name=kind value=name tabindex=-1 aria-hidden=true style='position:absolute;left:-9999px'></button>{guess}{chips}"
                "<details style='margin-top:10px'><summary class=meta>都不是，输入新名字</summary>"
                "<input type=text name=name placeholder='只填姓名，如：示例人物'>"
                "<input type=text name=role placeholder='岗位/单位（可空），如：示例岗位' style='margin-top:8px'>"
                "<div class=btns><button class=p name=kind value=name>确认新名字</button></div></details>"
                "<div class=btns><button name=kind value=self" + (" class=p" if sug == '本人' else '') + ">是我本人</button>"
                "<button name=kind value=mixed>混了两个人</button>"
                "<button name=kind value=unknown>不认识</button></div></form>")
    else:
        a = it.get('answer') or {}
        label = {'name': f"已确认：{a.get('name')}" + (f"（{a.get('role')}）" if a.get('role') else ''), 'self': '已确认：主人本人', 'mixed': '标记为混了两个人', 'unknown': '不认识'}.get(a.get('kind'), '')
        form = (f"<div class=pre>{e(label)}<br><span class=meta>{e(it.get('resolved_at'))} · 来自{e(it.get('resolved_via'))}</span></div>"
                f"<form method=post action='/u/{it['id']}'><div class=btns><button>撤销</button></div></form>")
    cls = 'card' + (' done' if it['status'] != 'open' else '')
    return (f"<div class='{cls}' id='{it['id']}'><div class=q>{e(it['question'])}</div>"
            f"<div class=meta>{e(p.get('summary'))}</div>{audio}{ev}{target}{form}</div>")

def page(show):
    its = items(); open_ = [i for i in its if i['status'] == 'open']; done = [i for i in its if i['status'] != 'open']
    lst = open_ if show == 'open' else done[::-1]
    body = ''.join(card_html(i) for i in lst) or "<div class=card>没有待确认的事项。</div>"
    return (f"<!doctype html><html lang=zh><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<meta name=apple-mobile-web-app-capable content=yes><meta name=apple-mobile-web-app-title content='确认中心'>"
            f"<link rel=manifest href=/manifest.json><title>确认中心</title><style>{CSS}</style>"
            f"<header>确认中心<small>待确认 {len(open_)} · 已处理 {len(done)}</small></header>"
            f"<nav><a href='/'>待确认</a><a href='/?show=done'>已处理</a></nav>{body}</html>")

# ---------- v0.2 网页（自托管，替代 CloudBase）：/kb/ 静态页 + /kbc JSON 接口 ----------
SITE = Path('/opt/kb-confirm/site')
WEBKEY_F = ROOT / 'web.key'
WEBKEYS = [KEY] + ([WEBKEY_F.read_text().strip()] if WEBKEY_F.exists() else [])
def keyok(k): return bool(k) and any(secrets.compare_digest(str(k), x) for x in WEBKEYS)

_INGESTION_CACHE = {'signature': None, 'state': {}}

def ingestion_for(it):
    try:
        path = ROOT / 'ingestion.json'
        stat = path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        if _INGESTION_CACHE['signature'] != signature:
            state = json.loads(path.read_text(encoding='utf-8'))
            _INGESTION_CACHE.update(signature=signature, state=state)
        state = _INGESTION_CACHE['state']
        result = state.get('cards', {}).get(it['id'])
        if result:
            result = dict(result, checked_at=state.get('at'))
            if time.time() - state.get('at', 0) > 300:
                result['stale'] = True
                result['label'] += '（回执检查已过期）'
            return result
    except (FileNotFoundError, ValueError, OSError): pass
    return None

def vocabulary_view():
    from kb_dictionary_cache import read
    cached = read()
    if not cached: return {'entries': [], 'available': False, 'notice': '词典缓存暂不可用；没有重新推断释义。'}
    d = cached['dictionary']
    return {'entries': [dict(e) for e in d['entries'] if e.get('active', True)], 'version': d.get('version'),
            'available': True, 'source': '20-Wiki/综合/已确认词典.md', 'notice': '只读已确认词条；词义确认不代表业务已上线。'}


def doc(it):
    ch = it.get('change') or {}; p = it.get('payload') or {}
    card = {k: it.get(k) for k in ('question', 'evidence', 'target', 'draft', 'choices')}
    card.update({k: p.get(k) for k in ('summary', 'label', 'title', 'page', 'refs', 'score')})
    # Correct derived display strings only; do not rename paths or change card identity.
    if it.get('type') != 'kb_question':
        from kb_dictionary_cache import read
        from kb_confirmed_dictionary import normalize_prose
        cached = read()
        if cached:
            for field in ('question', 'summary', 'title'):
                if isinstance(card.get(field), str):
                    card[field], _ = normalize_prose(card[field], cached['dictionary'])
    return {'id': it['id'], 'type': it.get('type'), 'status': it['status'], 'answer': it.get('answer'),
            'status_label': it.get('status_label'), 'auto_closed': it.get('auto_closed', False),
            'resolved_at': it.get('resolved_at'), 'resolved_via': it.get('resolved_via'), 'created': it.get('created'), 'batch': it.get('batch'),
            'write': ch.get('write'), 'write_due': ch.get('due'), 'written_at': ch.get('written_at'), 'withdrawn': len(it.get('history', [])),
            'card': card, 'pending': False, 'ingestion': ingestion_for(it),
            'audioUrls': ['?a=audio&n=' + quote(a) for a in it.get('assets', []) if (ASSETS / a).is_file()]}

def api_list():
    pr = predictions()
    return {'items': [doc(i) for i in items()], 'people': pr.get('people', []), 'predict': pr.get('items', {}), 'synced': now()}

def api_act(b):
    a = b.get('a'); iid = str(b.get('id') or '')
    if a not in ('resolve', 'undo'): return 404, {'error': 'not found'}
    if not re.fullmatch(r'[A-Za-z0-9_\-]+', iid) or not (ITEMS / f'{iid}.json').exists(): return 404, {'error': '没有这张卡片'}
    it = load(iid)
    if a == 'undo':
        if it['status'] not in ('resolved', 'snoozed'): return 409, {'error': '这张卡片还没有处理'}
        undo(iid, '网页')
    else:
        if it['status'] != 'open': return 409, {'error': '这张卡片已经处理过了'}
        if it['type'] == 'kb_question':
            kind = str(b.get('kind') or ''); text = str(b.get('text') or '').strip()[:2000]
            if kind not in Q_KINDS: return 400, {'error': '不认识的回答'}
            if kind == 'fix' and not text: return 400, {'error': '请写下你知道的说法'}
            if kind == 'choose' and text not in (it.get('choices') or []): return 400, {'error': '没有这个选项'}
            if kind == 'confirm' and not ((it.get('draft') or {}).get('text')): return 400, {'error': '这题没有草拟答案，请直接写'}
            ans = {'kind': kind, 'text': text or None}
        else:
            kind = b.get('kind'); name = str(b.get('name') or '').strip()[:40]; role = str(b.get('role') or '').strip()[:60]; pick = str(b.get('pick') or '').strip()
            if pick:
                known = {x['name']: x.get('role') for x in predictions().get('people', [])}
                if pick not in known: return 400, {'error': '名单里没有这个名字'}
                kind, name, role = 'name', pick, known[pick] or ''
            if kind not in ('name', 'self', 'mixed', 'unknown') or (kind == 'name' and not name): return 400, {'error': '请填写名字'}
            ans = {'kind': kind, 'name': name if kind == 'name' else None, 'role': role or None, 'picked': bool(pick)}
        _, ok = resolve(iid, ans, '网页')
        if not ok: return 409, {'error': '这张卡片已经处理过了'}
    if it['type'] != 'kb_question': repredict()
    return 200, {'ok': True}


class H(BaseHTTPRequestHandler):
    def authed(self):
        c = self.headers.get('Cookie', '')
        return f'kbk={KEY}' in c
    def send(self, code, body=b'', ctype='text/html; charset=utf-8', extra=None):
        self.send_response(code); self.send_header('Content-Type', ctype)
        self.send_header('Cache-Control', 'no-store'); self.send_header('X-Robots-Tag', 'noindex')
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers(); self.wfile.write(body if isinstance(body, bytes) else body.encode())
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == '/kb':
            return self.send(301, '', extra={'Location': '/kb/' + (('?' + u.query) if u.query else '')})
        if u.path == '/kb/':
            return self.send(200, (SITE / 'kb' / 'index.html').read_bytes())
        if u.path == '/kb/config.js':
            return self.send(200, "window.KBC_API = '/kbc';", 'application/javascript')
        if u.path == '/kbc':
            if not keyok(q.get('k', [''])[0]): return self.send(404, json.dumps({'error': 'not found'}), 'application/json')
            a = q.get('a', [''])[0]
            if a == 'list': return self.send(200, json.dumps(api_list(), ensure_ascii=False), 'application/json; charset=utf-8')
            if a == 'vocabulary': return self.send(200, json.dumps(vocabulary_view(), ensure_ascii=False), 'application/json; charset=utf-8')
            if a == 'audio':
                n = q.get('n', [''])[0]
                if '..' in n or not re.fullmatch(r'[A-Za-z0-9_\-./]+', n or '-'): return self.send(404, 'not found', 'text/plain')
                return self.serve_asset(ASSETS / n)
            return self.send(404, json.dumps({'error': 'not found'}), 'application/json')
        if u.path == '/' and not u.query:
            return self.send(302, '', extra={'Location': '/kb/'})
        if u.path == f'/k/{KEY}':
            return self.send(303, '', extra={'Location': f'/kb/?k={KEY}', 'Set-Cookie': f'kbk={KEY}; Max-Age=31536000; Path=/; Secure; HttpOnly; SameSite=Lax'})
        if u.path == '/manifest.json':
            return self.send(200, json.dumps({'name': '确认中心', 'start_url': '/', 'display': 'standalone', 'background_color': '#f4f3ef'}), 'application/json')
        m = re.fullmatch(r'/a/([A-Za-z0-9_\-./]+)', u.path)
        if m and '..' not in m.group(1) and (self.authed() or parse_qs(u.query).get('k', [''])[0] == KEY):
            return self.serve_asset(ASSETS / m.group(1))
        if not self.authed(): return self.send(404, 'not found', 'text/plain')
        if u.path in ('/', '/old'):
            return self.send(200, page(parse_qs(u.query).get('show', ['open'])[0]))
        return self.send(404, 'not found', 'text/plain')
    def serve_asset(self, f):
        # Stream just the requested bytes, never read a whole asset for a Range probe.
        try: f.resolve().relative_to(ASSETS.resolve())
        except ValueError: return self.send(404, 'not found', 'text/plain')
        if not f.is_file(): return self.send(404, 'not found', 'text/plain')
        size = f.stat().st_size
        ctype = 'audio/mp4' if f.suffix == '.m4a' else (mimetypes.guess_type(f.name)[0] or 'application/octet-stream')
        rg = re.fullmatch(r'bytes=(\d*)-(\d*)', self.headers.get('Range', '') or '')
        a, b, code = 0, size - 1, 200
        if rg and (rg[1] or rg[2]):
            a = int(rg[1]) if rg[1] else max(size - int(rg[2]), 0)
            b = min(int(rg[2]), size - 1) if rg[1] and rg[2] else size - 1
            if a > b or a >= size: return self.send(416, '', extra={'Content-Range': f'bytes */{size}'})
            code = 206
        self.send_response(code); self.send_header('Content-Type', ctype)
        self.send_header('Accept-Ranges', 'bytes'); self.send_header('Content-Length', str(max(0, b-a+1)))
        if code == 206: self.send_header('Content-Range', f'bytes {a}-{b}/{size}')
        self.send_header('Cache-Control', 'private, max-age=3600'); self.send_header('X-Robots-Tag', 'noindex'); self.end_headers()
        if self.command == 'HEAD': return
        try:
            with f.open('rb') as src:
                src.seek(a); left = b-a+1
                while left > 0:
                    block = src.read(min(left, 65536))
                    if not block: break
                    self.wfile.write(block); left -= len(block)
        except (BrokenPipeError, ConnectionResetError): pass

    def do_HEAD(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        if u.path == '/kbc' and keyok(q.get('k', [''])[0]) and q.get('a', [''])[0] == 'audio':
            n = q.get('n', [''])[0]
            if '..' not in n and re.fullmatch(r'[A-Za-z0-9_\-./]+', n or '-'): return self.serve_asset(ASSETS / n)
        return self.send(404, b'', 'text/plain')

    def do_POST(self):
        if urlparse(self.path).path == '/kbc':
            n = int(self.headers.get('Content-Length', 0) or 0)
            try: b = json.loads(self.rfile.read(min(n, 65536)).decode() or '{}')
            except Exception: b = {}
            if not isinstance(b, dict) or not keyok(b.get('k')): return self.send(404, json.dumps({'error': 'not found'}), 'application/json')
            code, obj = api_act(b)
            return self.send(code, json.dumps(obj, ensure_ascii=False), 'application/json; charset=utf-8')
        if not self.authed(): return self.send(404, 'not found', 'text/plain')
        n = int(self.headers.get('Content-Length', 0) or 0); f = parse_qs(self.rfile.read(min(n, 8192)).decode())
        m = re.fullmatch(r'/(r|u)/([A-Za-z0-9_\-]+)', self.path)
        if not m or not (ITEMS / f'{m.group(2)}.json').exists(): return self.send(404, 'not found', 'text/plain')
        if m.group(1) == 'r':
            kind = f.get('kind', [''])[0]; name = f.get('name', [''])[0].strip()[:40]; role = f.get('role', [''])[0].strip()[:60]
            pick = f.get('pick', [''])[0].strip()
            if pick:
                known = {x['name']: x.get('role') for x in predictions().get('people', [])}
                if pick not in known: return self.send(303, '', extra={'Location': f'/#{m.group(2)}'})
                kind, name, role = 'name', pick, known[pick] or ''
            if kind not in ('name', 'self', 'mixed', 'unknown') or (kind == 'name' and not name):
                return self.send(303, '', extra={'Location': f'/#{m.group(2)}'})
            resolve(m.group(2), {'kind': kind, 'name': name if kind == 'name' else None, 'role': role or None, 'picked': bool(pick)}, '网页')
        else:
            undo(m.group(2), '网页')
        repredict()
        return self.send(303, '', extra={'Location': '/old'})
    def log_message(self, *a): pass

if __name__ == '__main__':
    ThreadingHTTPServer(('localhost', 18890), H).serve_forever()


