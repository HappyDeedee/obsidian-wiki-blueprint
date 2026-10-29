"""Local, isolated installation and confirmation UI. No production service calls.

The local reference vault must not be shared with a sync engine. The original
multi-service implementation is provided separately under production-reference.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse

from wiki import Wiki, WikiError, atomic, canonical, digest, init as init_vault, note, read_json, save_json

REPO = Path(__file__).resolve().parents[1]
MAX_BODY = 65536


def stamp():
    return datetime.now(timezone.utc).isoformat()


def home_path(path):
    root = Path(path).absolute()
    if any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction()) for p in [root, *root.parents]):
        raise WikiError('Symlink/junction installation paths are not supported')
    return root


def checked_tree(root):
    """Do not follow an injected link into files outside an installation/backup."""
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in [*dirs, *files]:
            p = Path(parent)/name
            if p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction()):
                raise WikiError('Symlink/junction inside installation or backup is not supported')


def install(home, demo=False):
    root = home_path(home)
    if root.exists() and any(root.iterdir()):
        raise WikiError('Installation needs a NEW empty directory; existing data is never reset')
    root.mkdir(parents=True, exist_ok=True)
    init_vault(root / 'vault')
    private = root / 'private'
    private.mkdir(mode=0o700)
    password = secrets.token_urlsafe(32)
    save_json(private / 'access.json', {'password': password, 'created_at': stamp()})
    try:
        os.chmod(private / 'access.json', 0o600)
    except OSError:
        pass
    save_json(root / 'installation.json', {'schema': 1, 'profile': 'isolated-local',
              'vault_name': 'MyWiki', 'created_at': stamp(), 'demo': bool(demo)})
    save_json(private / 'cards.json', {'items': {}, 'events': [], 'vocabulary': []})
    if demo:
        from demo import run, TARGET
        run(root / 'vault')
        Wiki(root / 'vault', 'owner').question('web-demo-001', TARGET,
            '【虚构示例】演示项目下一次测试的预算是否已经明确？')
        state = read_json(private / 'cards.json')
        state['items']['speaker-demo-001'] = {
            'id': 'speaker-demo-001', 'type': 'speaker_identity', 'status': 'open',
            'card': {'summary': '虚构会议 · 界面演示', 'question': '演示发言人应该如何称呼？',
                     'evidence': [{'text': '这是预写示例，没有读取真实录音，也没有进行声纹识别。', 'source': '虚构示例'}]},
            'audioUrls': [], 'demo_only': True}
        save_json(private / 'cards.json', state)
    return {'status': 'INITIALIZED', 'profile': 'isolated-local', 'home': str(root),
            'login_secret_file': str(private / 'access.json'), 'model_or_sync_enabled': False}


class Center:
    def __init__(self, home):
        self.home = home_path(home)
        self.config = read_json(self.home / 'installation.json')
        if self.config.get('profile') != 'isolated-local':
            raise WikiError('This server only supports the isolated-local profile')
        self.vault = Wiki(self.home / 'vault', 'owner')
        self.state_file = self.home / 'private/cards.json'
        self.lock = threading.RLock()
        self.password = read_json(self.home / 'private/access.json')['password']
        self.sessions = {}

    def _state(self):
        return read_json(self.state_file)

    def _save(self, state, action, item_id):
        state['events'].append({'at': stamp(), 'action': action, 'id': item_id})
        save_json(self.state_file, state)

    def listing(self):
        with self.lock:
            state = self._state()
            items = [dict(x) for x in state['items'].values() if x.get('type') != 'kb_question']
            for p in sorted((self.vault.state / 'questions').glob('*.json')):
                q = read_json(p)
                ui = state['items'].get(q['question_id'], {})
                target = next(iter(q['target_versions']))
                item = {'id': q['question_id'], 'type': 'kb_question',
                        'question_version': q['question_version'],
                        'status': 'open' if q['status'] == 'pending' else 'resolved',
                        'card': {'question': q['ask'], 'page': target, 'title': target.split('/')[-1], 'label': '知识'},
                        'write': None}
                if ui:
                    item.update({k: v for k, v in ui.items() if k not in {'card', 'type', 'id'}})
                # The authoritative final receipt is validated by Wiki.resolve, not a UI flag.
                if q['status'] == 'adopted':
                    item.update(status='resolved', write='done', ingestion={
                        'state': 'ingested', 'label': '已入库（本地参考回执）',
                        'pages': list(q['target_versions']), 'reason': '正式提交与答复对应，回执核验通过。'})
                elif q['status'] == 'received':
                    item.update(status='resolved', write='done', ingestion={
                        'state': 'received', 'label': '已收到，待整理核证', 'pages': [],
                        'reason': '尚未有最终知识提交回执，不能显示已入库。'})
                    if not item.get('answer'):
                        answers = [read_json(a) for a in (self.vault.state / 'answers').glob('*.json')]
                        match = next((a for a in answers if a['question_id'] == q['question_id']), None)
                        if match:
                            item['answer'] = {'kind': 'fix', 'text': match['text']}
                items.append(item)
            return {'items': items, 'people': [], 'predict': {}, 'profile': 'isolated-local',
                    'notice': '本地参考；不连接作者服务。发言人演示不代表声纹已部署。'}

    def act(self, action, body):
        iid = body.get('id', '')
        with self.lock:
            state = self._state()
            listing = {i['id']: i for i in self.listing()['items']}
            if iid not in listing:
                raise WikiError('Unknown item')
            current = listing[iid]
            saved = state['items'].setdefault(iid, {'id': iid, 'type': current['type']})
            if action == 'undo':
                # This reference never erases a received answer or rewrites a historical receipt.
                if current['type'] == 'kb_question' and self.vault._question(iid)['status'] != 'pending':
                    raise WikiError('已投稿/已入库答复须创建可追溯的更正提案；本地参考不执行历史删除')
                for key in ['status', 'answer', 'resolved_at', 'write_due', 'kind']:
                    saved.pop(key, None)
                if current['type'] != 'kb_question':
                    saved['status'] = 'open'
                self._save(state, 'undo', iid)
                return {'status': 'REOPENED'}
            if action != 'resolve':
                raise WikiError('Unsupported action')
            kind = body.get('kind')
            if current['type'] != 'kb_question':
                if not current.get('demo_only'):
                    raise WikiError('Voice service is not enabled in this profile')
                if kind not in {'name', 'self', 'unknown', 'mixed'}:
                    raise WikiError('Unsupported speaker action')
                name = str(body.get('name', '')).strip()
                if kind == 'name' and not name:
                    raise WikiError('Name required')
                saved.update(status='resolved', answer={'kind': kind, 'name': name, 'role': str(body.get('role',''))},
                             resolved_at=stamp(), pending=True)
                self._save(state, 'demo_speaker_only', iid)
                return {'status': 'DEMO_ONLY', 'voice_model_called': False, 'person_page_written': False}
            q = self.vault._question(iid)
            if body.get('question_version') != q['question_version']:
                raise WikiError('STALE_QUESTION; reload the card')
            if kind not in {'fix', 'choose', 'forgot', 'invalid', 'later'}:
                raise WikiError('Explicit user text required; this reference has no automatic draft approval')
            if q['status'] != 'pending':
                raise WikiError('Question already received or adopted; review before another answer')
            if kind in {'later', 'invalid', 'forgot'}:
                saved.update(status='snoozed' if kind == 'later' else 'resolved',
                             answer={'kind': kind}, resolved_at=stamp(), write='done',
                             ingestion={'state': 'not_adopted', 'label': '保留未知/关闭界面项，未写正式知识', 'pages': []})
                self._save(state, kind, iid)
                return {'status': 'NOT_ADOPTED'}
            text = str(body.get('text', '')).strip()
            if not text or len(text) > 12000:
                raise WikiError('Answer text is required and must be at most 12000 characters')
            answer = self.vault.answer(iid, q['question_version'], text,
                'confirmation-center://authenticated-local-owner/' + iid + '/' + str(q['question_version']))
            saved.update(status='resolved', answer={'kind': kind, 'text': text}, resolved_at=stamp(),
                         write='done', answer_path=answer['path'])
            self._save(state, 'answer_received', iid)
            return {'status': 'RECEIVED', 'path': answer['path'], 'adopted': False}


LOGIN = '''<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>确认中心登录</title><style>body{font:18px system-ui;background:#f5f2eb;margin:8vh auto;max-width:480px;padding:24px}input,button{box-sizing:border-box;width:100%;font:inherit;padding:12px;margin:12px 0}</style>
<h1>确认中心</h1><p>输入安装时在本机生成的访问密码。密码保存在安装目录的 private/access.json。</p>
<form method="post" action="/login"><label>本机访问密码<input name="password" type="password" required autocomplete="current-password"></label><button>进入</button></form>
<p>本地参考环境，不连接作者服务。</p></html>'''


def handler(center):
    class H(BaseHTTPRequestHandler):
        server_version = 'WikiLocal'

        def log_message(self, *args):
            pass  # Never log credentials, cookies or answer text.

        def host_ok(self):
            try:
                host = urlparse('http://' + self.headers.get('Host', '')).hostname
                return host == 'localhost' or ipaddress.ip_address(host).is_loopback
            except ValueError:
                return False

        def session(self):
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get('Cookie', ''))
                key = cookie['wiki_session'].value
                with center.lock:
                    entry = center.sessions.get(key)
                    if entry and entry['until'] > time.time():
                        return entry
            except (KeyError, ValueError):
                pass
            return None

        def send(self, status, value, content_type='application/json; charset=utf-8', extra=None):
            data = value if isinstance(value, bytes) else (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'same-origin')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; media-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not self.host_ok():
                return self.send(403, {'error': 'Loopback host required'})
            u = urlparse(self.path)
            if u.path == '/healthz':
                return self.send(200, {'status': 'ok', 'profile': 'isolated-local'})
            session = self.session()
            if not session:
                if u.path in {'/', '/kb/', '/login'}:
                    return self.send(200, LOGIN, 'text/html; charset=utf-8')
                return self.send(401, {'error': 'Login required'})
            if u.path in {'/', '/kb/'}:
                return self.send(200, (REPO/'system/confirmation-ui/index.html').read_bytes(), 'text/html; charset=utf-8')
            if u.path in {'/config.js', '/kb/config.js'}:
                js = 'window.KBC_CONFIG=' + json.dumps({'api': '/kbc', 'csrf': session['csrf'], 'vault': center.config['vault_name'], 'profile': 'isolated-local'}) + ';'
                return self.send(200, js, 'application/javascript')
            if u.path == '/kbc':
                action = parse_qs(u.query).get('a', [''])[0]
                if action == 'list':
                    return self.send(200, center.listing())
                if action == 'vocabulary':
                    return self.send(200, {'entries': center._state()['vocabulary'], 'warning': '本地参考只显示示例词条；生产词典需按搭建手册接入。'})
            return self.send(404, {'error': 'Not found'})

        def do_POST(self):
            if not self.host_ok():
                return self.send(403, {'error': 'Loopback host required'})
            origin = self.headers.get('Origin')
            if origin and origin != 'http://' + self.headers.get('Host', ''):
                return self.send(403, {'error': 'Origin denied'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if size < 1 or size > MAX_BODY:
                    return self.send(413, {'error': 'Invalid body size'})
                data = self.rfile.read(size).decode('utf-8')
                if self.path == '/login':
                    candidate = parse_qs(data).get('password', [''])[0]
                    if not hmac.compare_digest(candidate.encode(), center.password.encode()):
                        return self.send(401, '密码不正确。', 'text/plain; charset=utf-8')
                    key = secrets.token_urlsafe(32)
                    with center.lock:
                        center.sessions = {k:v for k,v in center.sessions.items() if v['until'] > time.time()}
                        center.sessions[key] = {'csrf': secrets.token_urlsafe(32), 'until': time.time()+28800}
                    return self.send(303, '', extra={'Location': '/kb/', 'Set-Cookie': 'wiki_session='+key+'; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800'})
                session = self.session()
                if not session:
                    return self.send(401, {'error': 'Login required'})
                if not hmac.compare_digest(self.headers.get('X-Wiki-CSRF', ''), session['csrf']):
                    return self.send(403, {'error': 'CSRF denied'})
                if self.path != '/kbc':
                    return self.send(404, {'error': 'Not found'})
                body = json.loads(data)
                if not isinstance(body, dict):
                    raise ValueError('Object required')
                return self.send(200, center.act(body.get('a'), body))
            except (WikiError, ValueError, KeyError, TypeError) as exc:
                return self.send(409, {'error': str(exc)})
            except OSError:
                return self.send(503, {'error': 'Storage unavailable; inspect local state before retrying'})

    return H


def make_server(home, port=8765):
    center = Center(home)
    server = ThreadingHTTPServer(('localhost', port), handler(center))
    server.daemon_threads = True
    server.timeout = 1
    return server


def doctor(home=None):
    result = {'python': sys.version.split()[0], 'node_available': bool(shutil.which('node')),
              'docker_cli_available': bool(shutil.which('docker')), 'production_readiness': 'NOT_CERTIFIED',
              'checks': {}}
    if home:
        root = home_path(home)
        center = Center(root)
        result['checks'] = {'installation_marker': True, 'ui_exists': (REPO/'system/confirmation-ui/index.html').is_file(),
                            'reference_health': center.vault.health(), 'cards': len(center.listing()['items'])}
    return result


def backup(home, destination):
    root = home_path(home)
    Center(root)  # Verify this is our installation.
    target = home_path(destination)
    if target.is_relative_to(root):
        raise WikiError('Backup must be outside the installation')
    if target.exists():
        raise WikiError('Backup destination already exists')
    checked_tree(root)
    # Stop UI and all writers first. Directory lock excludes cooperating CLI writers.
    with Wiki(root/'vault', 'owner').locked():
        shutil.copytree(root, target, ignore=shutil.ignore_patterns('writer.lock'))
    hashes = {p.relative_to(target).as_posix(): digest(p.read_bytes()) for p in target.rglob('*') if p.is_file()}
    save_json(target/'backup-manifest.json', {'at': stamp(), 'files': hashes, 'contains_secrets': True})
    return {'status': 'COPIED', 'destination': str(target), 'contains_secrets': True,
            'requires_stopped_ui_and_writers': True}


def restore(source, home):
    src, target = home_path(source), home_path(home)
    if target.exists():
        raise WikiError('Restore only to a NEW non-existing directory')
    if target.is_relative_to(src):
        raise WikiError('Restore destination must be outside the backup')
    checked_tree(src)
    manifest = read_json(src/'backup-manifest.json')
    actual = {p.relative_to(src).as_posix(): digest(p.read_bytes()) for p in src.rglob('*') if p.is_file() and p != src/'backup-manifest.json'}
    if manifest['files'] != actual:
        raise WikiError('Backup manifest mismatch')
    shutil.copytree(src, target)
    (target/'backup-manifest.json').unlink()
    Center(target)
    return {'status': 'RESTORED', 'home': str(target), 'file_hashes': 'PASS'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('init'); p.add_argument('--home', required=True); p.add_argument('--demo', action='store_true')
    p = sub.add_parser('serve'); p.add_argument('--home', required=True); p.add_argument('--port', type=int, default=8765)
    p = sub.add_parser('launch'); p.add_argument('--home', required=True); p.add_argument('--port', type=int, default=8765); p.add_argument('--demo', action='store_true')
    p = sub.add_parser('doctor'); p.add_argument('--home')
    p = sub.add_parser('backup'); p.add_argument('--home', required=True); p.add_argument('--destination', required=True)
    p = sub.add_parser('restore'); p.add_argument('--source', required=True); p.add_argument('--home', required=True)
    args = parser.parse_args()
    if args.command == 'init': result = install(args.home, args.demo)
    elif args.command == 'doctor': result = doctor(args.home)
    elif args.command == 'backup': result = backup(args.home, args.destination)
    elif args.command == 'restore': result = restore(args.source, args.home)
    else:
        if args.command == 'launch' and not (home_path(args.home)/'installation.json').exists():
            install(args.home, args.demo)
        server = make_server(args.home, args.port)
        print(json.dumps({'status': 'LOCAL_UI_READY', 'url': 'http://localhost:'+str(server.server_port)+'/kb/',
                         'password_file': str(Path(args.home)/'private/access.json'),
                         'profile': 'isolated-local', 'sync_or_cloud_enabled': False}, ensure_ascii=False), flush=True)
        try: server.serve_forever(poll_interval=0.25)
        except KeyboardInterrupt: pass
        finally: server.server_close()
        return
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    try: main()
    except (WikiError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({'status': 'ERROR', 'reason': str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
