import hashlib
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import sys
import urllib.error
import urllib.parse
import urllib.request

# Repository test layout; deployed services install these modules together.
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'confirm'))
from receiver import BoundedServer, Capture, handler_for


RESULTS = {
    'Transcription': {'Transcription': {'Paragraphs': [
        {'SpeakerId': '1', 'Words': [{'Text': '下周确认预算。', 'Start': 100, 'End': 2000, 'SentenceId': 1}]}]}},
    'Summarization': {'Summarization': {'ParagraphSummary': '讨论预算确认。'}},
    'MeetingAssistance': {'MeetingAssistance': {'Actions': [
        {'Text': '确认预算', 'SentenceId': 1, 'Start': 100, 'End': 2000}]}},
    'AutoChapters': {'AutoChapters': [{'Headline': '预算确认', 'Summary': '讨论未确定的预算。'}]},
    'CustomPrompt': {'CustomPrompt': [{'Name': 'meeting-minutes-v2', 'Result': json.dumps({
        'title': '演示项目预算研讨', 'overview': '预算仍需确认。',
        'topics': [{'title':'预算', 'discussion':'下周确认预算。', 'conclusion':'金额尚未确定。'}],
        'decisions': [], 'open_questions': ['预算待确认'],
        'action_items': [{'task':'确认预算', 'owner':None, 'deadline':'下周', 'evidence_sentence_ids':[1]}]
    },ensure_ascii=False)}]},
}


class Provider:
    submissions = 0
    fail_submit = False
    fail_download_once = False
    missing_result = False
    file_url = None

    def __init__(self, config):
        pass

    def submit(self, file_url, job_id):
        type(self).submissions += 1
        type(self).file_url = file_url
        if type(self).fail_submit:
            raise TimeoutError('Cloud might already have accepted the request')
        return 'fake-task-id'

    def poll(self, task_id):
        keys = ['Transcription'] if type(self).missing_result else RESULTS
        return {'TaskStatus': 'COMPLETED', 'Result': {key: 'https://example.invalid/'+key for key in keys}}

    def fetch_result(self, url):
        if type(self).fail_download_once:
            type(self).fail_download_once = False
            raise TimeoutError()
        return RESULTS[url.rsplit('/', 1)[1]]


class ReceiverTests(unittest.TestCase):
    def test_export_retry_preserves_cloud_result_and_does_not_resubmit(self):
        job = self.upload()
        self.ready()
        self.capture.tick()
        self.capture.update(job, next_poll=0)
        self.capture.tick()
        self.settings.update(obsidian_mcp_url='http://localhost/mcp', obsidian_mcp_token='fake')
        self.save_config()
        calls = []
        class Exporter:
            def __init__(self, config):
                pass
            def export(self, job_id):
                calls.append(job_id)
                if len(calls) == 1:
                    raise TimeoutError('import might already have saved the files')
                return {'saved': True, 'job_id': job_id, 'audio_sha256': job_id,
                        'note_path': '00-Inbox/会议/test.md'}
        self.capture.exporter_factory = Exporter
        self.capture.tick()
        self.assertEqual(self.capture.get(job)['state'], 'completed')
        self.assertEqual(self.capture.get(job)['obsidian_state'], 'retry')
        self.assertEqual(self.request('/jobs/'+job+'/result')[0], 200)
        restarted = Capture(self.config, Provider, Exporter)
        restarted.update(job, next_export=0)
        restarted.tick()
        self.assertEqual(restarted.get(job)['obsidian_state'], 'saved')
        restarted.tick()
        self.assertEqual(len(calls), 2)
        self.assertEqual(Provider.submissions, 1)
        self.assertTrue((self.capture.root/(job+'.wav')).exists())

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config = self.root/'config.json'
        self.settings = {'data_dir': str(self.root/'data'), 'phone_token': 'test-phone-token',
                         'signing_secret': 'test-signing-secret', 'public_base_url': 'https://example.invalid',
                         'max_upload_mb': 1, 'max_storage_mb': 3,
                         'app_key': '', 'access_key_id': '', 'access_key_secret': ''}
        self.save_config()
        Provider.submissions = 0
        Provider.fail_submit = Provider.fail_download_once = Provider.missing_result = False
        self.capture = Capture(self.config, Provider)
        self.server = BoundedServer(('localhost', 0), handler_for(self.capture))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://localhost:' + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temporary.cleanup()

    def save_config(self):
        self.config.write_text(json.dumps(self.settings), encoding='utf-8')

    def ready(self):
        self.settings.update(app_key='fake-app', access_key_id='fake-id', access_key_secret='fake-secret')
        self.save_config()

    def request(self, path, body=None, token=True):
        headers = {'X-Audio-Extension': 'wav'}
        if token:
            headers['Authorization'] = 'Bearer test-phone-token'
        request = urllib.request.Request(self.base+path, data=body, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as response:
            return response.code, json.load(response)

    def upload(self):
        return self.request('/upload', b'fake audio payload')[1]['job_id']

    def test_installer_download_exposes_only_fixed_public_file(self):
        installer = b'AEA1-test-installer-with-no-credentials'
        for filename in ('meeting-recording.shortcut', 'meeting-upload.shortcut', 'meeting-native-trial.shortcut', 'meeting-start.shortcut', 'meeting-output-check.shortcut'):
            self.assertEqual(self.request('/install/'+filename, token=False)[0], 404)
            (self.root/filename).write_bytes(installer)
            with urllib.request.urlopen(self.base+'/install/'+filename, timeout=5) as response:
                self.assertEqual(response.read(), installer)
                self.assertEqual(int(response.headers['Content-Length']), len(installer))
                if filename == 'meeting-native-trial.shortcut':
                    self.assertIn(urllib.parse.quote('会议录音-语音备忘录试用.shortcut'), response.headers['Content-Disposition'])
        for path in ('/config.json', '/install/config.json', '/install/../config.json'):
            self.assertEqual(self.request(path, token=False)[0], 401)

    def test_http_upload_is_persisted_and_deduplicated_without_cloud(self):
        code, first = self.request('/upload', b'fake audio payload')
        self.assertEqual(code, 202)
        self.assertEqual(first['state'], 'waiting_config')
        self.assertEqual(first['sha256'], hashlib.sha256(b'fake audio payload').hexdigest())
        self.assertEqual((self.capture.root/(first['job_id']+'.wav')).read_bytes(), b'fake audio payload')
        code, duplicate = self.request('/upload', b'fake audio payload')
        self.assertEqual(code, 200)
        self.assertTrue(duplicate['duplicate'])
        self.capture.tick()
        self.assertEqual(Provider.submissions, 0)

    def test_upload_and_results_require_authentication(self):
        self.assertEqual(self.request('/upload', b'audio', token=False)[0], 401)
        self.assertEqual(self.request('/jobs', token=False)[0], 401)
        self.assertEqual(self.request('/health', token=False)[0], 200)

    def test_empty_oversized_and_incomplete_uploads_do_not_queue(self):
        self.assertEqual(self.request('/upload', b'')[0], 400)
        # Reject an oversized declared length before reading a body. Sending a
        # megabyte with urllib races the server closing the rejected request;
        # a BrokenPipeError would hide the HTTP response we want to assert.
        connection = http.client.HTTPConnection('localhost', self.server.server_port, timeout=5)
        try:
            connection.request('POST', '/upload', headers={
                'Authorization': 'Bearer test-phone-token',
                'X-Audio-Extension': 'wav',
                'Content-Length': str(1024*1024+1),
            })
            response = connection.getresponse()
            self.assertEqual(response.status, 400)
            self.assertEqual(json.load(response)['error'], 'invalid_audio_size')
        finally:
            connection.close()
        with self.assertRaisesRegex(ValueError, 'incomplete_upload'):
            self.capture.accept(io.BytesIO(b'x'), 20, 'wav')
        self.assertEqual(self.request('/jobs')[1]['jobs'], [])
        self.assertEqual(list(self.capture.root.glob('*.upload')), [])

    def test_capacity_limit_rejects_upload_without_removing_existing_audio(self):
        job = self.upload()
        self.settings['max_storage_mb'] = 0
        self.save_config()
        self.assertEqual(self.request('/upload', b'other audio')[0], 400)
        self.assertTrue((self.capture.root/(job+'.wav')).exists())

    def test_signed_audio_url_expiry_and_content_length(self):
        job = self.upload()
        signed = urllib.parse.urlsplit(self.capture.signed_audio_url(self.capture.get(job)))
        with urllib.request.urlopen(self.base+signed.path+'?'+signed.query) as response:
            body = response.read()
            self.assertEqual(int(response.headers['Content-Length']), len(body))
            self.assertEqual(body, b'fake audio payload')
        self.assertEqual(self.request(signed.path+'?expires=1&signature=bad', token=False)[0], 403)
        self.assertIsNone(self.capture.audio_path('/audio/../../config.json', signed.query))

    def test_hot_configuration_completes_full_mock_pipeline(self):
        job = self.upload()
        self.assertEqual(self.request('/jobs/'+job+'/result')[0], 409)
        self.ready()
        self.capture.tick()
        self.capture.update(job, next_poll=0)
        self.capture.tick()
        code, result = self.request('/jobs/'+job+'/result')
        self.assertEqual(code, 200)
        self.assertEqual(result['transcript'], '下周确认预算。')
        self.assertEqual(result['summary'], '讨论预算确认。')
        self.assertEqual(result['actions'][0]['Text'], '确认预算')
        self.assertNotIn('Assignee', result['actions'][0])
        self.assertEqual(result['raw_results'], RESULTS)
        self.assertEqual(Provider.submissions, 1)

    def test_uncertain_submission_is_not_retried_or_double_billed(self):
        job = self.upload()
        self.ready()
        Provider.fail_submit = True
        self.capture.tick()
        self.capture.tick()
        self.assertEqual(self.capture.get(job)['state'], 'submission_uncertain')
        self.assertEqual(Provider.submissions, 1)
        self.assertTrue((self.capture.root/(job+'.wav')).exists())

    def test_restart_preserves_accepted_tasks_and_flags_interrupted_submission(self):
        job = self.upload()
        self.capture.update(job, state='submitting')
        restarted = Capture(self.config, Provider)
        self.assertEqual(restarted.get(job)['state'], 'submission_uncertain')
        self.capture.update(job, state='processing', task_id='saved-task')
        self.assertEqual(Capture(self.config, Provider).get(job)['task_id'], 'saved-task')

    def test_result_download_retry_does_not_submit_again(self):
        job = self.upload()
        self.ready()
        self.capture.tick()
        self.capture.update(job, next_poll=0)
        Provider.fail_download_once = True
        self.capture.tick()
        self.assertEqual(self.capture.get(job)['state'], 'processing')
        self.capture.update(job, next_poll=0)
        self.capture.tick()
        self.assertEqual(self.capture.get(job)['state'], 'completed')
        self.assertEqual(Provider.submissions, 1)

    def test_missing_requested_output_cannot_be_marked_completed(self):
        job = self.upload()
        self.ready()
        self.capture.tick()
        self.capture.update(job, next_poll=0)
        Provider.missing_result = True
        self.capture.tick()
        self.assertEqual(self.capture.get(job)['state'], 'failed')
        self.assertEqual(self.request('/jobs/'+job+'/result')[0], 409)


if __name__ == '__main__':
    unittest.main()
