"""Authenticated localhost MCP calls; never logs credentials or meeting contents."""
import json
import urllib.request

class Exporter:
    def __init__(self, config):
        self.url = config['obsidian_mcp_url']
        self.token = config['obsidian_mcp_token']
        self.session = None
        self.number = 0

    def call(self, method, params=None, notify=False):
        self.number += 1
        body = {'jsonrpc': '2.0', 'method': method}
        if not notify:
            body['id'] = self.number
        if params is not None:
            body['params'] = params
        headers = {'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json',
                   'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': '2024-11-05'}
        if self.session:
            headers['Mcp-Session-Id'] = self.session
        request = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers=headers)
        with urllib.request.urlopen(request, timeout=180) as response:
            self.session = response.headers.get('Mcp-Session-Id') or self.session
            raw = response.read(1024*1024).decode()
        if notify:
            return None
        if raw.startswith(('event:', 'data:')):
            values = [json.loads(line[5:].strip()) for line in raw.splitlines() if line.startswith('data:')]
            result = next(value for value in values if value.get('id') == self.number)
        else:
            result = json.loads(raw)
        if 'error' in result:
            # Keep a diagnostic code without exposing token, signed URL or meeting content.
            import re
            message=str(result['error'].get('message',''))
            safe=re.findall(r'\b(?:meeting_|source_|invalid_)[a-z_]+\b',message)
            error=ValueError('obsidian_rpc_failed')
            error.diagnostic={'rpc_code':result['error'].get('code'),'safe_codes':safe,
                'error_types':re.findall(r'\b[A-Za-z]+Error\b',message)}
            raise error
        return result['result']

    def export(self, job_id):
        try:
            self.call('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
                                    'clientInfo': {'name': 'meeting-capture', 'version': '2'}})
            self.call('notifications/initialized', notify=True)
            result = self.call('tools/call', {'name': 'import_meeting', 'arguments': {'job_id': job_id}})
            if result.get('isError'):
                raise ValueError('obsidian_import_failed')
            content = json.loads('\n'.join(item.get('text', '') for item in result.get('content', [])))
            if content.get('saved') is not True or content.get('job_id') != job_id or content.get('audio_sha256') != job_id:
                raise ValueError('obsidian_import_unverified')
            return content
        finally:
            # Release each completed session; do not accumulate server sessions forever.
            if self.session:
                request = urllib.request.Request(self.url, method='DELETE', headers={
                    'Authorization': 'Bearer ' + self.token, 'Mcp-Session-Id': self.session})
                try:
                    with urllib.request.urlopen(request, timeout=10):
                        pass
                except Exception:
                    pass
