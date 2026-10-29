"""Small audio receiver with asynchronous Tingwu processing and Obsidian export."""
import argparse
from contextlib import contextmanager
import datetime as dt
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from mcp_export import Exporter
from meeting_analysis import ANALYSIS_VERSION, analysis_parameters, normalize_enhanced, required_results

STATES = {
    'queued': '已上传，等待提交听悟',
    'waiting_config': '已上传，等待配置听悟',
    'submitting': '正在提交听悟',
    'submission_uncertain': '提交结果不确定，需要核对听悟任务，录音已保留',
    'processing': '听悟正在处理',
    'waiting_quota': '听悟额度不足，录音已保留，等待自动重试',
    'completed': '转写、摘要和待办已获取',
    'failed': '处理失败，录音已保留',
}
REQUIRED_RESULTS = ('Transcription', 'Summarization', 'MeetingAssistance')
JOB_PATTERN = re.compile(r'^[a-f0-9]{64}$')
QUOTA_ERROR = 'PRE.AudioDurationQuotaLimit'


def validate_source_path(path, extension):
    prefix = '00-Inbox/会议/待处理/'
    name = path.removeprefix(prefix)
    if (not path.startswith(prefix) or not name or '/' in name or '\\' in name
            or any(char in name for char in ('\n', '\r', '\x00', '[', ']', '|', '#', '^'))
            or not name.lower().endswith('.' + extension)):
        raise ValueError('invalid_source_audio_path')
    return path


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def normalize(results, version=1):
    transcription = results['Transcription'].get('Transcription', {})
    if not isinstance(transcription.get('Paragraphs'), list):
        raise ValueError('result_schema_unknown')
    paragraphs = []
    for paragraph in transcription['Paragraphs']:
        words = paragraph.get('Words', [])
        paragraphs.append({
            'speaker_id': paragraph.get('SpeakerId'),
            'text': ''.join(word.get('Text', '') for word in words),
            'start_ms': words[0].get('Start') if words else None,
            'end_ms': words[-1].get('End') if words else None,
        })
    summary = results['Summarization'].get('Summarization', {})
    assistance = results['MeetingAssistance'].get('MeetingAssistance')
    if (not isinstance(summary, dict)
            or not isinstance(assistance, dict) or not isinstance(assistance.get('Actions', []), list)):
        raise ValueError('result_schema_unknown')
    enhanced = normalize_enhanced(results, version)
    if 'ParagraphSummary' in summary:
        paragraph_summary = summary['ParagraphSummary']
    elif version >= 2 and enhanced.get('minutes'):
        # A completed task can return other summaries but omit ParagraphSummary.
        # Keep validated custom minutes and all raw outputs instead of losing the meeting.
        paragraph_summary = enhanced['minutes']['overview']
        enhanced['analysis_warnings'].append('听悟未返回全文摘要，纪要采用自定义会议概述；其他已返回分析保留。')
    else:
        raise ValueError('result_schema_unknown')
    return {
        'transcript': '\n\n'.join(item['text'] for item in paragraphs),
        'paragraphs': paragraphs,
        'summary': paragraph_summary,
        'actions': assistance.get('Actions', []),
        'raw_results': results,
        **enhanced,
    }


class Tingwu:
    """Uses the Alibaba SDK exactly as in the official Tingwu Python samples."""
    def __init__(self, config):
        from aliyunsdkcore.client import AcsClient
        from aliyunsdkcore.auth.credentials import AccessKeyCredential
        credentials = AccessKeyCredential(config['access_key_id'], config['access_key_secret'])
        self.client = AcsClient(region_id='cn-beijing', credential=credentials,
                               auto_retry=False, timeout=30)
        self.app_key = config['app_key']
        self.phrase_state_path = Path(config['data_dir']) / 'tingwu-phrases.state.json'
        self.dictionary_cache_path = Path(config['data_dir']) / 'confirmed-dictionary.cache.json'

    def confirmed_parameters(self):
        from kb_dictionary_cache import read
        from kb_tingwu_phrases import PhraseManager
        cached = read(self.dictionary_cache_path)
        if not cached:return analysis_parameters()
        def load():
            if not self.phrase_state_path.exists():return {}
            return json.loads(self.phrase_state_path.read_text(encoding='utf-8'))
        def save(value):
            temporary = self.phrase_state_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
            temporary.replace(self.phrase_state_path)
        def api(method, path, body):return self.request(method, path, body, task_type=None)
        try:
            state = PhraseManager(api, load, save).sync(cached['dictionary'])
        except Exception:
            # Phrase permission/transport failure does not stop the existing ASR lane.
            state = load()
        return analysis_parameters(dictionary=cached['dictionary'], phrase_state=state)

    def request(self, method, path, body=None, task_type='offline'):
        from aliyunsdkcore.request import CommonRequest
        request = CommonRequest()
        request.set_accept_format('json')
        request.set_domain('tingwu.cn-beijing.aliyuncs.com')
        request.set_version('2023-09-30')
        request.set_protocol_type('https')
        request.set_method(method)
        request.set_uri_pattern(path)
        if body is not None:
            if task_type is not None:request.add_query_param('type', task_type)
            request.add_header('Content-Type', 'application/json')
            request.set_content(json.dumps(body).encode('utf-8'))
        data = json.loads(self.client.do_action_with_exception(request))
        if data.get('Code') not in ('0', 0):
            raise ValueError('tingwu_api_error')
        return data['Data']

    def submit(self, file_url, job_id):
        data = self.request('PUT', '/openapi/tingwu/v2/tasks', {
            'AppKey': self.app_key,
            'Input': {'FileUrl': file_url, 'SourceLanguage': 'cn', 'TaskKey': job_id},
            'Parameters': self.confirmed_parameters(),
        })
        return data['TaskId']

    def poll(self, task_id):
        return self.request('GET', '/openapi/tingwu/v2/tasks/' + urllib.parse.quote(task_id, safe=''))

    def rerun(self, task_id):
        parameters = analysis_parameters()
        # Tingwu cannot rerun ASR or change diarization on an existing task.
        parameters.pop('Transcription', None)
        return self.request('PUT', '/openapi/tingwu/v2/tasks', {
            'AppKey': self.app_key, 'Input': {'TaskId': task_id},
            'Parameters': parameters,
        })

    def fetch_result(self, url):
        if urllib.parse.urlsplit(url).scheme not in ('http', 'https'):
            raise ValueError('invalid_result_url')
        with urllib.request.urlopen(url, timeout=45) as response:
            body = response.read(32 * 1024 * 1024 + 1)
        if len(body) > 32 * 1024 * 1024:
            raise ValueError('result_too_large')
        return json.loads(body)


class Capture:
    def __init__(self, config_path, provider_factory=Tingwu, exporter_factory=Exporter):
        self.config_path = Path(config_path)
        initial = self.config()
        self.root = Path(initial['data_dir'])
        self.root.mkdir(parents=True, exist_ok=True)
        self.provider_factory = provider_factory
        self.exporter_factory = exporter_factory
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.wake = threading.Event()
        with self.database() as database:
            database.execute('''CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, extension TEXT, bytes INTEGER, created TEXT,
                state TEXT, task_id TEXT, error TEXT DEFAULT '', next_poll REAL DEFAULT 0)''')
            # A crash after cloud acceptance must not automatically create a second paid task.
            database.execute("UPDATE jobs SET state='submission_uncertain' WHERE state='submitting'")
            columns = {row['name'] for row in database.execute('PRAGMA table_info(jobs)')}
            for name, declaration in {
                'obsidian_state': "TEXT DEFAULT 'waiting'", 'obsidian_path': "TEXT DEFAULT ''",
                'obsidian_error': "TEXT DEFAULT ''", 'next_export': 'REAL DEFAULT 0',
                'source_path': "TEXT DEFAULT ''", 'source_revision': "TEXT DEFAULT ''",
                'analysis_version': 'INTEGER DEFAULT 1',
                'upgrade_previous_sha256': "TEXT DEFAULT ''",
                'cloud_error_code': "TEXT DEFAULT ''", 'retry_count': 'INTEGER DEFAULT 0',
            }.items():
                if name not in columns:
                    database.execute(f'ALTER TABLE jobs ADD COLUMN {name} {declaration}')
            database.execute('''CREATE TABLE IF NOT EXISTS cloud_attempts (
                task_id TEXT PRIMARY KEY, job_id TEXT, task_key TEXT,
                status TEXT, error_code TEXT, recorded REAL)''')

    @contextmanager
    def database(self):
        database = sqlite3.connect(self.root / 'jobs.sqlite3', timeout=15)
        database.row_factory = sqlite3.Row
        try:
            with database:
                yield database
        finally:
            database.close()

    def config(self):
        return json.loads(self.config_path.read_text(encoding='utf-8-sig'))

    def ready(self):
        config = self.config()
        return all(config.get(key) for key in ('app_key', 'access_key_id', 'access_key_secret'))

    def get(self, job_id):
        with self.database() as database:
            return database.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()

    def update(self, job_id, **values):
        allowed = {'state', 'task_id', 'error', 'next_poll', 'obsidian_state',
                   'obsidian_path', 'obsidian_error', 'next_export', 'analysis_version',
                   'cloud_error_code', 'retry_count'}
        if not values.keys() <= allowed:
            raise ValueError('invalid_update')
        with self.database() as database:
            database.execute('UPDATE jobs SET ' + ','.join(key+'=?' for key in values) + ' WHERE id=?',
                             [*values.values(), job_id])

    def public(self, row):
        base = self.config()['public_base_url'].rstrip('/')
        message = '纪要和原始录音已存入 Obsidian' if row['obsidian_state'] == 'saved' else STATES[row['state']]
        return {'job_id': row['id'], 'state': row['state'], 'message': message,
                'bytes': row['bytes'], 'sha256': row['id'], 'created': row['created'],
                'task_id': row['task_id'], 'error': row['error'],
                'cloud_error_code': row['cloud_error_code'], 'retry_count': row['retry_count'],
                'obsidian_state': row['obsidian_state'], 'obsidian_path': row['obsidian_path'],
                'obsidian_error': row['obsidian_error'],
                'status_url': base + '/jobs/' + row['id'],
                'result_url': base + '/jobs/' + row['id'] + '/result'}

    def accept(self, stream, size, extension, source_path='', source_revision=''):
        config = self.config()
        if source_path:
            validate_source_path(source_path, extension)
            if not re.fullmatch(r'[0-9]+-[a-zA-Z0-9]+', source_revision):
                raise ValueError('invalid_source_revision')
        if size < 1 or size > config.get('max_upload_mb', 250)*1024*1024:
            raise ValueError('invalid_audio_size')
        if extension not in ('m4a', 'mp3', 'wav', 'aac', 'caf', 'mp4'):
            raise ValueError('invalid_audio_extension')
        with self.lock:
            used = sum(path.stat().st_size for path in self.root.iterdir() if path.is_file())
            if used + size > config.get('max_storage_mb', 3072)*1024*1024:
                raise ValueError('storage_quota_reached')
            if shutil.disk_usage(self.root).free < size + 512*1024*1024:
                raise ValueError('disk_space_low')
            digest = hashlib.sha256()
            temporary_name = None
            try:
                with tempfile.NamedTemporaryFile(dir=self.root, suffix='.upload', delete=False) as output:
                    temporary_name = output.name
                    remaining = size
                    while remaining:
                        chunk = stream.read(min(remaining, 64*1024))
                        if not chunk:
                            raise ValueError('incomplete_upload')
                        remaining -= len(chunk)
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                job_id = digest.hexdigest()
                row = self.get(job_id)
                if row:
                    return row, True
                os.replace(temporary_name, self.root / (job_id + '.' + extension))
                temporary_name = None
                created = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat()
                state = 'queued' if self.ready() else 'waiting_config'
                with self.database() as database:
                    database.execute('INSERT INTO jobs (id,extension,bytes,created,state,source_path,source_revision,analysis_version) VALUES (?,?,?,?,?,?,?,?)',
                                     (job_id, extension, size, created, state, source_path, source_revision, ANALYSIS_VERSION))
                self.wake.set()
                return self.get(job_id), False
            finally:
                if temporary_name:
                    Path(temporary_name).unlink(missing_ok=True)

    def signed_audio_url(self, row):
        config = self.config()
        # Trial console allows 24 h completion; leave download time beyond that window.
        expires = int(time.time()) + 48*60*60
        name = row['id'] + '.' + row['extension']
        signature = hmac.new(config['signing_secret'].encode(), f'{name}:{expires}'.encode(),
                             hashlib.sha256).hexdigest()
        return f"{config['public_base_url'].rstrip('/')}/audio/{name}?expires={expires}&signature={signature}"

    def audio_path(self, path, query):
        name = path.removeprefix('/audio/')
        if not re.fullmatch(r'[a-f0-9]{64}\.(m4a|mp3|wav|aac|caf|mp4)', name):
            return None
        try:
            params = urllib.parse.parse_qs(query)
            expires = int(params['expires'][0])
            signature = params['signature'][0]
        except (ValueError, KeyError):
            return None
        expected = hmac.new(self.config()['signing_secret'].encode(), f'{name}:{expires}'.encode(),
                            hashlib.sha256).hexdigest()
        if expires < time.time() or not hmac.compare_digest(signature, expected):
            return None
        file = self.root / name
        return file if file.is_file() else None

    def tick(self):
        try:
            self.sync_hotwords()
            self.cloud_tick()
        finally:
            self.export_tick()

    def sync_hotwords(self):
        # Reuse this single worker: no thread, new task, or model call. Inspect
        # on dictionary revision/day changes even when no new audio arrives.
        cache = self.root / 'confirmed-dictionary.cache.json'
        if not cache.exists() or not self.ready():
            return
        today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
        key = (cache.stat().st_mtime_ns, today)
        if getattr(self, '_phrase_check_key', None) == key:
            return
        self._phrase_check_key = key
        try:
            self.provider_factory(self.config()).confirmed_parameters()
        except Exception:
            # Failures stay in phrase state; audio intake remains available.
            print(json.dumps({'event': 'tingwu_phrases_deferred'}), flush=True)

    def export_tick(self):
        config = self.config()
        if not config.get('obsidian_mcp_url') or not config.get('obsidian_mcp_token'):
            return
        with self.database() as database:
            rows = database.execute("SELECT * FROM jobs WHERE state='completed' AND obsidian_state!='saved' AND next_export<=? ORDER BY created LIMIT 1", (time.time(),)).fetchall()
        for row in rows:
            job_id = row['id']
            try:
                atomic_json(self.root / f'{job_id}.export.json', {
                    'job_id': job_id, 'task_id': row['task_id'], 'extension': row['extension'],
                    'created': row['created'], 'bytes': row['bytes'],
                    'is_test': job_id in config.get('synthetic_test_job_ids', []),
                    'source_path': row['source_path'], 'source_revision': row['source_revision'],
                    'note_path': row['obsidian_path'],
                    'upgrade_previous_sha256': row['upgrade_previous_sha256'],
                })
                receipt = self.exporter_factory(config).export(job_id)
                atomic_json(self.root / f'{job_id}.obsidian.json', receipt)
                self.update(job_id, obsidian_state='saved', obsidian_path=receipt['note_path'], obsidian_error='', next_export=0)
            except Exception:
                # Do not put tool error messages, tokens or meeting contents in logs.
                self.update(job_id, obsidian_state='retry', obsidian_error='obsidian_export_retry', next_export=time.time()+60)
                print(json.dumps({'event': 'obsidian_export_retry', 'job_id': job_id}), flush=True)

    def cloud_tick(self):
        if not self.ready():
            return
        with self.database() as database:
            rows = database.execute("SELECT * FROM jobs WHERE state IN ('queued','waiting_config','processing','waiting_quota') AND next_poll<=? ORDER BY created LIMIT 5", (time.time(),)).fetchall()
        if not rows:
            return
        provider = self.provider_factory(self.config())
        for row in rows:
            job_id = row['id']
            if row['state'] == 'waiting_quota':
                # Only a known terminal quota rejection can create a new cloud task.
                # Timeouts, uncertain submissions and other failures never take this path.
                try:
                    if row['task_id']:
                        failed = provider.poll(row['task_id'])
                        if failed.get('TaskStatus') != 'FAILED' or failed.get('ErrorCode') != QUOTA_ERROR:
                            self.update(job_id, state='processing', error='', next_poll=0)
                            continue
                    elif row['cloud_error_code'] != QUOTA_ERROR:
                        self.update(job_id, state='submission_uncertain', error='quota_retry_without_known_rejection')
                        continue
                    audio = self.root / (job_id + '.' + row['extension'])
                    digest = hashlib.sha256()
                    with audio.open('rb') as source:
                        for chunk in iter(lambda: source.read(65536), b''):
                            digest.update(chunk)
                    if audio.stat().st_size != row['bytes'] or digest.hexdigest() != job_id:
                        self.update(job_id, state='failed', error='retained_audio_changed_preserved')
                        continue
                    with self.database() as database:
                        task_key = job_id if not row['retry_count'] else f'{job_id}-retry-{row["retry_count"]}'
                        if row['task_id']:
                            database.execute('INSERT INTO cloud_attempts VALUES(?,?,?,?,?,?) '
                                'ON CONFLICT(task_id) DO UPDATE SET status=excluded.status,error_code=excluded.error_code',
                                (row['task_id'], job_id, task_key, 'FAILED', QUOTA_ERROR, time.time()))
                        database.execute("UPDATE jobs SET state='queued',task_id='',retry_count=retry_count+1,"
                                         "cloud_error_code='',error='',next_poll=0 WHERE id=?", (job_id,))
                    row = self.get(job_id)
                except Exception:
                    self.update(job_id, error='quota_recheck_retry', next_poll=time.time()+60)
                    continue
            if not row['task_id']:
                self.update(job_id, state='submitting', analysis_version=ANALYSIS_VERSION)
                try:
                    task_key = job_id if not row['retry_count'] else f'{job_id}-retry-{row["retry_count"]}'
                    task_id = provider.submit(self.signed_audio_url(row), task_key)
                    if not task_id:
                        raise ValueError('missing_task_id')
                    self.update(job_id, state='processing', task_id=task_id, error='', next_poll=time.time()+30)
                    with self.database() as database:
                        database.execute('INSERT OR IGNORE INTO cloud_attempts VALUES(?,?,?,?,?,?)',
                            (task_id, job_id, task_key, 'SUBMITTED', '', time.time()))
                except Exception as error:
                    # Log only the SDK error code, never its message or signed file URL.
                    code_getter = getattr(error, 'get_error_code', None)
                    code = str(code_getter()) if callable(code_getter) else type(error).__name__
                    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,120}', code):
                        code = 'unknown_error'
                    print(json.dumps({'event': 'tingwu_submit_failed', 'code': code}), flush=True)
                    if code == QUOTA_ERROR:
                        delay = max(60, int(self.config().get('quota_retry_seconds', 3600)))
                        self.update(job_id, state='waiting_quota', cloud_error_code=code,
                                    error='tingwu_quota_waiting_retry', next_poll=time.time()+delay)
                    else:
                        self.update(job_id, state='submission_uncertain', error='submit_failed_check_cloud_task')
                continue
            try:
                data = provider.poll(row['task_id'])
                status = data['TaskStatus']
                if status in ('FAILED', 'INVALID'):
                    code = data.get('ErrorCode', '')
                    if not isinstance(code, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,120}', code):
                        code = ''
                    if status == 'FAILED' and code == QUOTA_ERROR:
                        delay = max(60, int(self.config().get('quota_retry_seconds', 3600)))
                        self.update(job_id, state='waiting_quota', error='tingwu_quota_waiting_retry',
                                    cloud_error_code=code, next_poll=time.time()+delay)
                        print(json.dumps({'event':'tingwu_quota_waiting', 'job_id':job_id,
                                          'retry_after_seconds':delay}), flush=True)
                    else:
                        self.update(job_id, state='failed', error='tingwu_task_' + status.lower(), cloud_error_code=code)
                elif status == 'COMPLETED':
                    urls = data.get('Result', {})
                    expected = required_results(row['analysis_version'])
                    if not all(urls.get(key) for key in expected):
                        self.update(job_id, state='failed', error='missing_requested_results')
                        continue
                    results = {}
                    for key in expected:
                        path = self.root / f'{job_id}.{key}.json'
                        if path.exists():
                            results[key] = json.loads(path.read_text(encoding='utf-8'))
                        else:
                            results[key] = provider.fetch_result(urls[key])
                            atomic_json(path, results[key])
                    result = normalize(results, row['analysis_version'])
                    result.update({'job_id': job_id, 'task_id': row['task_id']})
                    atomic_json(self.root / f'{job_id}.result.json', result)
                    self.update(job_id, state='completed', error='')
                else:
                    self.update(job_id, state='processing', error='', next_poll=time.time()+60)
            except ValueError as error:
                if str(error) == 'result_schema_unknown':
                    self.update(job_id, state='failed', error='result_schema_unknown_raw_saved')
                else:
                    self.update(job_id, error='poll_or_result_fetch_retry', next_poll=time.time()+60)
            except Exception:
                self.update(job_id, error='poll_or_result_fetch_retry', next_poll=time.time()+60)

    def work(self):
        while not self.stop.is_set():
            try:
                self.tick()
            except Exception:
                print('worker_configuration_or_sdk_error', flush=True)
            self.wake.wait(15)
            self.wake.clear()


class BoundedServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, handler):
        self.slots = threading.BoundedSemaphore(4)
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        self.slots.acquire()
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def handler_for(capture):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(180)

        def log_message(self, *args):
            # Never log phone tokens, signed URLs or meeting contents.
            pass

        def respond(self, status, value):
            body = json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def authorized(self):
            expected = 'Bearer ' + capture.config()['phone_token']
            if not hmac.compare_digest(self.headers.get('Authorization', ''), expected):
                self.respond(401, {'error': 'unauthorized', 'message': '上传口令未填写或不正确'})
                return False
            return True

        def do_POST(self):
            if urllib.parse.urlsplit(self.path).path != '/upload':
                self.respond(404, {'error': 'not_found'})
                return
            if not self.authorized():
                return
            try:
                if self.headers.get('Transfer-Encoding'):
                    raise ValueError('content_length_required')
                size = int(self.headers.get('Content-Length', '0'))
                extension = self.headers.get('X-Audio-Extension', 'm4a').lower()
                source_path = urllib.parse.unquote(self.headers.get('X-Obsidian-Source', ''))
                source_revision = self.headers.get('X-Obsidian-Revision', '')
                row, duplicate = capture.accept(self.rfile, size, extension, source_path, source_revision)
                response = capture.public(row)
                response.update({'uploaded': True, 'duplicate': duplicate})
                self.respond(200 if duplicate else 202, response)
            except ValueError as error:
                code = str(error)
                self.respond(400, {'error': code if re.fullmatch('[a-z_]+', code) else 'invalid_upload',
                                   'message': '上传未完成，请保留手机副本后重试'})
            except Exception:
                self.respond(500, {'error': 'upload_failed', 'message': '上传失败，请保留手机副本后重试'})

        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            if path == '/health':
                config = capture.config()
                self.respond(200, {'ok': True, 'tingwu_configured': capture.ready(),
                                  'obsidian_export_configured': bool(config.get('obsidian_mcp_url') and config.get('obsidian_mcp_token')),
                                  'version': '2'})
                return
            installers = {
                '/install/meeting-recording.shortcut': ('meeting-recording.shortcut', '会议录音.shortcut'),
                '/install/meeting-upload.shortcut': ('meeting-upload.shortcut', '上传会议录音.shortcut'),
                '/install/meeting-native-trial.shortcut': ('meeting-native-trial.shortcut', '会议录音-语音备忘录试用.shortcut'),
                '/install/meeting-vault-phone.shortcut': ('meeting-vault-phone.shortcut', '会议录音.shortcut'),
                '/install/meeting-start.shortcut': ('meeting-start.shortcut', '会议录音-开始.shortcut'),
                '/install/meeting-output-check.shortcut': ('meeting-output-check.shortcut', '会议录音-文件类型检查.shortcut'),
            }
            if path in installers:
                # Fixed, non-secret signed installer only; never serve config or arbitrary files.
                filename, display_name = installers[path]
                installer = capture.config_path.parent / filename
                if not installer.is_file():
                    self.respond(404, {'error': 'installer_unavailable'})
                    return
                body = installer.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Disposition', 'attachment; filename=' + filename + "; filename*=UTF-8''" + urllib.parse.quote(display_name))
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(body)
                return
            if path.startswith('/audio/'):
                file = capture.audio_path(path, parsed.query)
                if file is None:
                    self.respond(403, {'error': 'invalid_or_expired_audio_url'})
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Length', str(file.stat().st_size))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                with file.open('rb') as stream:
                    shutil.copyfileobj(stream, self.wfile, length=64*1024)
                return
            if not self.authorized():
                return
            if path == '/jobs':
                with capture.database() as database:
                    rows = database.execute('SELECT * FROM jobs ORDER BY created DESC LIMIT 20').fetchall()
                self.respond(200, {'jobs': [capture.public(row) for row in rows]})
                return
            match = re.fullmatch(r'/jobs/([a-f0-9]{64})(/result)?', path)
            row = capture.get(match[1]) if match else None
            if row is None:
                self.respond(404, {'error': 'not_found'})
            elif match[2]:
                file = capture.root / f'{row["id"]}.result.json'
                if row['state'] != 'completed' or not file.exists():
                    self.respond(409, capture.public(row))
                else:
                    self.respond(200, json.loads(file.read_text(encoding='utf-8')))
            else:
                self.respond(200, capture.public(row))
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    capture = Capture(args.config)
    config = capture.config()
    server = BoundedServer((config.get('listen_host', 'localhost'), config.get('listen_port', 18889)),
                           handler_for(capture))
    threading.Thread(target=capture.work, daemon=True).start()
    print('meeting_receiver_started', flush=True)
    try:
        server.serve_forever()
    finally:
        capture.stop.set()
        capture.wake.set()
        server.server_close()


if __name__ == '__main__':
    main()
