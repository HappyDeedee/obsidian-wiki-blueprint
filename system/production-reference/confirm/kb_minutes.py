#!/usr/bin/env python3
"""把确认中心里已确认的发言人姓名写回会议纪要和完整转写（不使用 AI）。

- 只读 /var/lib/kb-voiceprint/voices.json 中 status=name / self 的声音；mixed 卡由拆分子卡代替。
- 幂等：每次先去掉旧名字再按当前确认结果重写，撤销后会恢复为听悟编号。
- 只在 voices.json 变化或每小时一次巡检时读笔记；内容不变就不写。
"""
import glob, hashlib, json, os, re, sys, time, urllib.request

VOICES = '/var/lib/kb-voiceprint/voices.json'
DATA = '/srv/wiki/meeting/data'
CONF = '/srv/wiki/meeting/config.json'
STATE = '/var/lib/kb-confirm/minutes_state.json'
LOG = '/var/lib/kb-confirm/log.jsonl'
SELF_NAME = '知识库主人（本人）'
FULL_EVERY = 3600
TOKEN = '/var/lib/kb-confirm/minutes_token'
ROOT = '00-Inbox/会议/'

SUM_LINE = '发言人编号由听悟自动识别，身份未经人工确认。'
TR_LINE = '发言人编号为自动识别结果。'
B0, B1 = '<!-- kb-speakers:begin -->', '<!-- kb-speakers:end -->'
BLOCK_RE = re.compile(re.escape(B0) + r'.*?' + re.escape(B1), re.S)


class MCP:
    def __init__(self, url, token):
        self.url, self.token, self.session, self.n = url, token, None, 0

    def call(self, method, params=None, notify=False):
        self.n += 1
        body = {'jsonrpc': '2.0', 'method': method}
        if not notify:
            body['id'] = self.n
        if params is not None:
            body['params'] = params
        h = {'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json',
             'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': '2024-11-05'}
        if self.session:
            h['Mcp-Session-Id'] = self.session
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers=h)
        with urllib.request.urlopen(req, timeout=120) as r:
            self.session = r.headers.get('Mcp-Session-Id') or self.session
            raw = r.read().decode()
        if notify:
            return None
        if raw.startswith(('event:', 'data:')):
            vals = [json.loads(l[5:].strip()) for l in raw.splitlines() if l.startswith('data:')]
            res = next(v for v in vals if v.get('id') == self.n)
        else:
            res = json.loads(raw)
        if 'error' in res:
            raise RuntimeError('rpc_error %s' % res['error'].get('code'))
        return res['result']

    def open(self):
        for i in range(3):
            try:
                self.call('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
                                         'clientInfo': {'name': 'kb-minutes', 'version': '1'}})
                self.call('notifications/initialized', notify=True)
                return
            except Exception:
                self.session = None
                time.sleep(2)
        raise RuntimeError('mcp_init_failed')

    def close(self):
        if self.session:
            try:
                urllib.request.urlopen(urllib.request.Request(self.url, method='DELETE', headers={
                    'Authorization': 'Bearer ' + self.token, 'Mcp-Session-Id': self.session}), timeout=10)
            except Exception:
                pass

    def tool(self, name, args):
        r = self.call('tools/call', {'name': name, 'arguments': args})
        text = '\n'.join(c.get('text', '') for c in r.get('content', []))
        if r.get('isError'):
            raise RuntimeError('tool_error %s: %s' % (name, text[:200]))
        return text

    def read(self, path):
        off, parts, total = 0, [], None
        while True:
            t = self.tool('read_note', {'path': path, 'offset': off, 'limit': 4000})
            m = re.search(r'\[Segment offset=(\d+) next_offset=(END|\d+) total_chars=(\d+)\]\n', t)
            if not m:
                raise RuntimeError('Missing pagination receipt; refusing to rewrite original')
            if int(m[1]) != off or (total is not None and int(m[3]) != total):
                raise RuntimeError('Original changed while reading')
            total = int(m[3]); parts.append(t[m.end():])
            if m[2] == 'END': break
            off = int(m[2])
        text = ''.join(parts)
        if len(text) != total: raise RuntimeError('Incomplete original')
        return text


def mapping():
    v = json.load(open(VOICES))
    out = {}
    for vid, x in v.items():
        st = x.get('status')
        if st not in ('name', 'self'):
            continue
        name = SELF_NAME if st == 'self' else (x.get('name') or '').strip()
        if not name:
            continue
        for m in x.get('members', []):
            out.setdefault(m['meeting'], {})[str(m['speaker'])] = (name, (x.get('role') or '').strip(), '主人确认')
    try:
        auto = json.load(open('/var/lib/kb-voiceprint/auto-matches.json'))
        anchor = hashlib.sha256(open(VOICES, 'rb').read()).hexdigest()
        if auto.get('voices_sha256') == anchor:
            for job, names in auto.get('matches', {}).items():
                for speaker, item in names.items():
                    out.setdefault(job, {}).setdefault(speaker, (item['name'], '', '声纹自动匹配'))
    except FileNotFoundError:
        pass
    return out


def sum_block(names):
    rows = [B0, '> [!info] 发言人身份（逐项标注识别来源）']
    for spk in sorted(names, key=lambda s: int(s) if s.isdigit() else 999):
        n, r, origin = names[spk]
        rows.append('> - 发言人%s：%s%s' % (spk, n, ('（%s）' % r if r else '') + '〔' + origin + '〕'))
    rows.append('> 未列出的发言人尚未确认，沿用听悟编号。')
    rows.append(B1)
    return '\n'.join(rows)


def tr_block(names):
    pairs = '；'.join('发言人 %s = %s〔%s〕' % (s, names[s][0], names[s][2]) for s in sorted(names, key=lambda s: int(s) if s.isdigit() else 999))
    return '%s\n发言人编号为自动识别结果；身份与识别来源：%s。其余沿用编号。\n%s' % (B0, pairs, B1)


def render_summary(text, names):
    def h(m):
        s = m.group(1)
        return '### 发言人%s · %s' % (s, names[s][0]) if s in names else '### 发言人%s' % s
    text = re.sub(r'^### 发言人(\d+)(?: · [^\n]*)?$', h, text, flags=re.M)
    new = sum_block(names) if names else SUM_LINE
    if BLOCK_RE.search(text):
        text = BLOCK_RE.sub(lambda m: new, text, count=1)
    elif SUM_LINE in text:
        text = text.replace(SUM_LINE, new, 1)
    return text


def render_transcript(text, names):
    def h(m):
        t, s = m.group(1), m.group(2)
        return '**%s · 发言人 %s · %s**' % (t, s, names[s][0]) if s in names else '**%s · 发言人 %s**' % (t, s)
    text = re.sub(r'^\*\*([\d:]+) · 发言人 (\d+)(?: · [^*\n]+)?\*\*$', h, text, flags=re.M)
    new = tr_block(names) if names else TR_LINE
    if BLOCK_RE.search(text):
        text = BLOCK_RE.sub(lambda m: new, text, count=1)
    elif TR_LINE in text:
        text = text.replace(TR_LINE, new, 1)
    return text


def meetings():
    out = {}
    for f in glob.glob(os.path.join(DATA, '*.obsidian.json')):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        if d.get('saved') and str(d.get('note_path', '')).startswith(ROOT):
            out[d['job_id']] = d
    return out


def sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


def log(**kw):
    kw['ts'] = time.strftime('%Y-%m-%d %H:%M:%S')
    kw['kind'] = 'minutes'
    with open(LOG, 'a') as f:
        f.write(json.dumps(kw, ensure_ascii=False) + '\n')


def main():
    force = '--force' in sys.argv
    dry = '--dry' in sys.argv
    vsha = hashlib.sha256(open(VOICES, 'rb').read() + (open('/var/lib/kb-voiceprint/auto-matches.json', 'rb').read() if os.path.exists('/var/lib/kb-voiceprint/auto-matches.json') else b'')).hexdigest()
    try:
        state = json.load(open(STATE))
    except Exception:
        state = {}
    if not force and state.get('voices_sha') == vsha and time.time() - state.get('full_at', 0) < FULL_EVERY:
        return
    conf = json.load(open(CONF))
    mcp = MCP(conf['obsidian_mcp_url'], open(TOKEN).read().strip())
    mcp.open()
    # Canonical note is the authority. The file is an atomic, hash-verified mirror.
    # This script is to be installed with kb_dictionary_cache on the same import path.
    from kb_dictionary_cache import read
    from kb_confirmed_dictionary import normalize_prose
    mp, changed, errors = mapping(), [], []
    try:
        dictionary = read()['dictionary']
        for job, d in sorted(meetings().items()):
            names = mp.get(job, {})
            for path, fn in ((d['note_path'], render_summary), (d.get('transcript_path'), render_transcript)):
                if not path:
                    continue
                try:
                    old = mcp.read(path)
                except Exception as e:
                    errors.append((path, str(e)[:120]))
                    continue
                new, corrections = normalize_prose(fn(old, names), dictionary, markdown=True)
                if new != old:
                    changed.append(path)
                    if not dry:
                        if mcp.read(path) != old: raise RuntimeError('Concurrent meeting edit; retry next timer')
                        backup = '/var/lib/kb-confirm/minutes-backups/' + sha(path)[:20] + '-' + sha(old) + '.md'
                        os.makedirs(os.path.dirname(backup), exist_ok=True)
                        if not os.path.exists(backup):
                            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                            with os.fdopen(fd, 'w') as f: f.write(old)
                        if corrections:log(path=path, spelling_corrections=corrections, backup=backup)
                        mcp.tool('write_note', {'path': path, 'content': new})
                        if mcp.read(path) != new:
                            errors.append((path, 'readback_mismatch'))
    finally:
        mcp.close()
    if not dry:
        state.update(voices_sha=vsha, full_at=time.time(), last_changed=changed, last_errors=errors)
        json.dump(state, open(STATE, 'w'), ensure_ascii=False, indent=1)
        if changed or errors:
            log(changed=changed, errors=errors)
    print(json.dumps({'changed': changed, 'errors': errors}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
