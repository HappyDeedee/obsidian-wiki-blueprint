"""Persist LiveSync arrival progress and feed complete audio into the existing receiver."""
import argparse
from contextlib import contextmanager
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import select
import signal
import sqlite3
import subprocess
import tempfile
import threading
import time
import urllib.parse

PREFIX = '00-Inbox/会议/待处理/'


def valid_path(path):
    name = path.removeprefix(PREFIX)
    return (path.startswith(PREFIX) and bool(name) and
            not re.search(r'[\\/\x00\r\n\[\]|#^]', name) and
            re.search(r'\.(m4a|mp3|wav|aac|caf|mp4)$', name, re.I) is not None)


class Bridge:
    def __init__(self, command, maximum):
        self.command = command
        self.maximum = maximum
        self.process = None
        self.buffer = bytearray()

    def close(self):
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.process = None
        self.buffer.clear()

    def fill(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([self.process.stdout], [], [], remaining)[0]:
            raise TimeoutError('bridge_timeout')
        chunk = os.read(self.process.stdout.fileno(), 65536)
        if not chunk:
            raise RuntimeError('bridge_disconnected')
        self.buffer.extend(chunk)

    def header(self, deadline):
        while b'\n' not in self.buffer:
            self.fill(deadline)
            if len(self.buffer) > 256*1024:
                raise RuntimeError('bridge_header_too_large')
        line, _, remaining = self.buffer.partition(b'\n')
        self.buffer = bytearray(remaining)
        result = json.loads(line)
        if result.get('ok') is not True:
            raise ValueError(result.get('error', 'bridge_failed'))
        return result

    def request(self, request, output=None):
        try:
            deadline = time.monotonic() + 120
            if self.process is None:
                print('{"event":"inbox_bridge_starting"}', flush=True)
                self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                stderr=subprocess.DEVNULL, bufsize=0)
                self.header(deadline)
                print('{"event":"inbox_bridge_ready"}', flush=True)
            self.process.stdin.write((json.dumps(request, ensure_ascii=False)+'\n').encode())
            self.process.stdin.flush()
            result = self.header(deadline)
            if request['op'] == 'read':
                remaining = result['size']
                if not output or not 0 < remaining <= self.maximum:
                    raise ValueError('invalid_bridge_audio_size')
                digest = hashlib.sha256()
                while remaining:
                    if not self.buffer:
                        self.fill(deadline)
                    block = bytes(self.buffer[:remaining])
                    del self.buffer[:len(block)]
                    remaining -= len(block)
                    digest.update(block)
                    output.write(block)
                if digest.hexdigest() != result['sha256']:
                    raise ValueError('bridge_audio_hash_mismatch')
            return result
        except Exception:
            self.close()
            raise


class Listener:
    def __init__(self, config_path, bridge=None):
        self.config_path = Path(config_path)
        self.config = json.loads(self.config_path.read_text(encoding='utf-8-sig'))
        self.root = Path(self.config['data_dir']) / 'inbox-listener'
        self.root.mkdir(mode=0o700, exist_ok=True)
        # Only this single-instance systemd service owns these transient staging files.
        for leftover in self.root.glob('*.part'):
            if leftover.is_file():
                leftover.unlink()
        self.maximum = min(int(self.config.get('max_inbox_audio_mb', 64)),
                           int(self.config.get('max_upload_mb', 250)), 128)*1024*1024
        self.provided_bridge = bridge is not None
        self.bridge = bridge or self.make_bridge()
        with self.database() as db:
            db.execute('CREATE TABLE IF NOT EXISTS progress (key TEXT PRIMARY KEY, value TEXT)')
            db.execute('''CREATE TABLE IF NOT EXISTS inputs (path TEXT PRIMARY KEY, revision TEXT, size INTEGER,
                mtime REAL, state TEXT, observed REAL, next_attempt REAL DEFAULT 0, attempts INTEGER DEFAULT 0,
                job_id TEXT DEFAULT '', error TEXT DEFAULT '')''')

    def make_bridge(self):
        return Bridge(['docker', 'exec', '-i', '-e', 'MEETING_AUDIO_MAX_BYTES='+str(self.maximum),
            'obsidian-mcp', 'node', '--expose-gc', '--max-old-space-size=384',
            '/app/meeting/inbox-audio-bridge.mjs'], self.maximum)

    def refresh_configuration(self):
        config = json.loads(self.config_path.read_text(encoding='utf-8-sig'))
        maximum = min(int(config.get('max_inbox_audio_mb', 64)),
                      int(config.get('max_upload_mb', 250)), 128)*1024*1024
        if maximum != self.maximum:
            self.maximum = maximum
            if not self.provided_bridge:
                self.bridge.close()
                self.bridge = self.make_bridge()
        self.config = config

    @contextmanager
    def database(self):
        db = sqlite3.connect(self.root/'progress.sqlite3', timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def observe(self, db, entry):
        path = entry['path']
        if not valid_path(path):
            return
        if entry.get('deleted'):
            db.execute("UPDATE inputs SET state='deleted' WHERE path=?", (path,))
            return
        existing = db.execute('SELECT revision FROM inputs WHERE path=?', (path,)).fetchone()
        if existing and existing['revision'] == entry['revision']:
            return
        state = 'waiting' if 0 < entry['size'] <= self.maximum else 'blocked_size'
        db.execute('''INSERT INTO inputs(path,revision,size,mtime,state,observed) VALUES(?,?,?,?,?,?)
            ON CONFLICT(path) DO UPDATE SET revision=excluded.revision,size=excluded.size,mtime=excluded.mtime,
            state=excluded.state,observed=excluded.observed,next_attempt=0,attempts=0,error='' ''',
            (path, entry['revision'], entry['size'], entry.get('mtime', 0), state, time.time()))

    def initialise(self):
        with self.database() as db:
            if db.execute("SELECT value FROM progress WHERE key='since'").fetchone():
                return
        # Establish the cutover first. Older tests are retained without being auto-submitted.
        head = self.bridge.request({'op':'head'})['since']
        entries = []
        for path in self.config.get('inbox_seed_paths', []):
            if not valid_path(path):
                raise ValueError('invalid_seed_path')
            entry = self.bridge.request({'op':'meta', 'path':path})['entry']
            if not entry:
                raise ValueError('seed_audio_not_found')
            entries.append(entry)
        with self.database() as db:
            for entry in entries:
                self.observe(db, entry)
            db.execute("INSERT INTO progress(key,value) VALUES('since',?)", (head,))

    def scan(self):
        self.initialise()
        with self.database() as db:
            since = db.execute("SELECT value FROM progress WHERE key='since'").fetchone()['value']
        # Bound each tick; persistent sequence continues the next tick if more batches remain.
        for _ in range(10):
            response = self.bridge.request({'op':'scan', 'since':since})
            with self.database() as db:
                for entry in response['entries']:
                    self.observe(db, entry)
                db.execute("UPDATE progress SET value=? WHERE key='since'", (response['since'],))
            since = response['since']
            if response['scanned'] < 100:
                break

    def send(self, file, header):
        connection = http.client.HTTPConnection(self.config.get('listen_host', 'localhost'),
                                              self.config.get('listen_port', 18889), timeout=180)
        try:
            connection.putrequest('POST', '/upload')
            for key, value in {'Authorization':'Bearer '+self.config['phone_token'],
                'Content-Length':str(header['size']), 'Content-Type':'application/octet-stream',
                'X-Audio-Extension':header['path'].rsplit('.',1)[1].lower(),
                'X-Obsidian-Source':urllib.parse.quote(header['path'], safe=''),
                'X-Obsidian-Revision':header['revision']}.items():
                connection.putheader(key, value)
            connection.endheaders()
            with file.open('rb') as audio:
                for chunk in iter(lambda:audio.read(65536), b''):
                    connection.send(chunk)
            response = connection.getresponse()
            data = json.loads(response.read(128*1024))
            if response.status not in (200, 202) or data.get('job_id') != header['sha256']:
                raise ValueError('receiver_did_not_accept_audio')
            return data['job_id']
        finally:
            connection.close()

    def process_one(self):
        now = time.time()
        with self.database() as db:
            # Persisted size blocks must recover after a limit change, even without
            # another LiveSync revision. The read below still verifies current metadata.
            db.execute("UPDATE inputs SET state='waiting',next_attempt=0,error='' "
                       "WHERE state='blocked_size' AND size>0 AND size<=?", (self.maximum,))
            row = db.execute("SELECT * FROM inputs WHERE state='waiting' AND next_attempt<=? AND observed<=? ORDER BY observed LIMIT 1",
                             (now, now-self.config.get('inbox_stability_seconds', 20))).fetchone()
        if not row:
            return
        temporary = None
        try:
            if row['size'] > self.maximum:
                with self.database() as db:
                    db.execute("UPDATE inputs SET state='blocked_size' WHERE path=?", (row['path'],))
                return
            if sum(p.stat().st_size for p in self.root.glob('*.part')) + row['size'] > 2*self.maximum:
                raise ValueError('listener_staging_full')
            with tempfile.NamedTemporaryFile(dir=self.root, suffix='.part', delete=False) as output:
                temporary = Path(output.name)
                header = self.bridge.request({'op':'read', 'path':row['path'], 'revision':row['revision']}, output)
                output.flush()
                os.fsync(output.fileno())
            if header['size'] != row['size'] or header['path'] != row['path'] or header['revision'] != row['revision']:
                raise ValueError('source_changed_during_read')
            job_id = self.send(temporary, header)
            with self.database() as db:
                db.execute("UPDATE inputs SET state='accepted',job_id=?,error='' WHERE path=? AND revision=?",
                           (job_id, row['path'], row['revision']))
            print(json.dumps({'event':'inbox_audio_accepted','sha256':job_id,'bytes':header['size']}), flush=True)
        except Exception as error:
            code = str(error) if re.fullmatch(r'[a-z_]{1,80}', str(error)) else 'audio_waiting_retry'
            if code in ('audio_changed_during_read', 'source_changed_during_read'):
                entry = self.bridge.request({'op':'meta','path':row['path']})['entry']
                with self.database() as db:
                    if entry:
                        self.observe(db, entry)
                    else:
                        db.execute("UPDATE inputs SET state='deleted' WHERE path=?", (row['path'],))
            else:
                with self.database() as db:
                    db.execute('UPDATE inputs SET attempts=attempts+1,next_attempt=?,error=? WHERE path=?',
                               (time.time()+min(300,30*2**min(row['attempts'],4)), code, row['path']))
            print(json.dumps({'event':'inbox_audio_waiting','error':code}), flush=True)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    def tick(self):
        self.refresh_configuration()
        self.scan()
        self.process_one()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    listener = Listener(args.config)
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_:stop.set())
    try:
        while not stop.is_set():
            try:
                listener.tick()
            except Exception as error:
                code = str(error) if re.fullmatch(r'[a-z_]{1,80}', str(error)) else 'scan_retry'
                print(json.dumps({'event':'inbox_scan_retry','error':code,'error_type':type(error).__name__}), flush=True)
            stop.wait(15)
    finally:
        listener.bridge.close()


if __name__ == '__main__':
    main()
