"""Disposable CI only: own generated Docker project, no real vault or model call."""
from pathlib import Path
import base64
import json
import secrets
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

from deploy import init, init_db

ROOT=Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix='wiki-stack-ci-') as tmp:
        private=Path(tmp)/'private';init(private,'https://ci.example.invalid')
        cfg=json.loads((private/'settings.private.json').read_text(encoding='utf-8'))
        project='wiki-ci-'+secrets.token_hex(6)
        command=['docker','compose','--project-name',project,'--env-file',str(private/'stack.env'),
                 '-f',str(ROOT/'system/deploy/compose.yaml')]
        def compose(*args):
            p=subprocess.run([*command,*args],capture_output=True,text=True,timeout=300)
            if p.returncode:raise RuntimeError('Owned CI Compose operation failed: '+args[0])
        def diagnostics():
            for args in [('ps','--all'),('logs','--no-color','--tail','35')]:
                p=subprocess.run([*command,*args],capture_output=True,text=True,timeout=30)
                out=(p.stdout+p.stderr)[-7000:]
                for value in cfg.values():
                    if len(value)>=16:out=out.replace(value,'[REDACTED_CI_VALUE]')
                print(out,flush=True)
        def request(url,method='GET',body=None,auth=None,session=None):
            headers={'Accept':'application/json, text/event-stream','Content-Type':'application/json'}
            if auth:headers['Authorization']=auth
            if session:headers['Mcp-Session-Id']=session
            data=None if body is None else json.dumps(body).encode()
            try:
                with urllib.request.urlopen(urllib.request.Request(url,data=data,method=method,headers=headers),timeout=15) as r:
                    raw=r.read().decode();obj=None
                    if raw:
                        obj=json.loads(next((s[6:] for s in raw.splitlines() if s.startswith('data: ')),raw))
                    return r.status,obj,r.headers.get('Mcp-Session-Id')
            except urllib.error.HTTPError as e:return e.code,None,None
        try:
            compose('config','--quiet');compose('up','-d','couchdb')
            couch='http://localhost:'+cfg['COUCHDB_LOCAL_PORT']
            admin='Basic '+base64.b64encode((cfg['COUCHDB_ADMIN_USER']+':'+cfg['COUCHDB_ADMIN_PASSWORD']).encode()).decode()
            last_status=None
            for _ in range(45):
                try:
                    last_status=request(couch+'/_up',auth=admin)[0]
                    if last_status==200:break
                except OSError:pass
                time.sleep(1)
            else:raise RuntimeError('CI CouchDB readiness failed; last HTTP status '+str(last_status))
            init_db(private)
            assert request(couch+'/wiki/_all_docs')[0] in (401,403),'Anonymous database read accepted'
            user='Basic '+base64.b64encode((cfg['LIVESYNC_USER']+':'+cfg['LIVESYNC_PASSWORD']).encode()).decode()
            assert request(couch+'/wiki/_all_docs',auth=user)[0]==200,'Sync user read failed'
            compose('up','-d','mcp-reader')
            mcp='http://localhost:'+cfg['MCP_LOCAL_PORT']+'/mcp'
            init_message={'jsonrpc':'2.0','id':1,'method':'initialize','params':{
                'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'wiki-blueprint-ci','version':'0.2.0'}}}
            auth='Bearer '+cfg['MCP_READER_TOKEN']
            for _ in range(60):
                try:
                    status,reply,session=request(mcp,'POST',init_message,auth)
                    if status==200 and reply and 'result' in reply:break
                except (OSError,ValueError):pass
                time.sleep(1)
            else:raise RuntimeError('CI MCP initialization failed')
            assert request(mcp,'POST',init_message)[0] in (401,403),'Anonymous MCP accepted'
            request(mcp,'POST',{'jsonrpc':'2.0','method':'notifications/initialized'},auth,session)
            status,reply,_=request(mcp,'POST',{'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}},auth,session)
            assert status==200 and 'result' in reply,'Tool listing failed'
            names={t['name'] for t in reply['result']['tools']}
            assert 'read_note' in names and not names.intersection({'write_note','edit_note','delete_note','move_note'}),'Read-only tool surface failed'
            status,reply,_=request(mcp,'POST',{'jsonrpc':'2.0','id':3,'method':'tools/call','params':{
                'name':'write_note','arguments':{'path':'should-not-exist.md','content':'ci'}}},auth,session)
            assert status==200 and ('error' in reply or reply.get('result',{}).get('isError')),'Read-only write accepted'
            print(json.dumps({'status':'PASS','scope':'disposable Docker CouchDB security and stock read-only MCP',
                              'real_phone_sync':False,'e2ee_attachment_roundtrip':False,'model_calls':0}))
        except Exception:
            diagnostics()
            raise
        finally:
            # Only volumes belonging to this random, newly created CI project.
            compose('down','--volumes','--remove-orphans')


if __name__=='__main__':main()
