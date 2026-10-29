"""Small authenticated MCP client for deployment checks and Markdown exports."""
import os
import urllib.request,urllib.error,urllib.parse,json,pathlib,uuid,sys,datetime,hashlib,base64,shutil,gzip

class MCP:
    def __init__(self,url,token,timeout=45):
        self.url=url; self.token=token; self.session=None; self.n=0; self.timeout=timeout
        self.call('initialize',{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'personal-kb-deployment','version':'1'}})
        self.call('notifications/initialized',None,notify=True)
    def call(self,method,params=None,notify=False):
        self.n+=1
        payload={'jsonrpc':'2.0','method':method}
        if not notify:payload['id']=self.n
        if params is not None:payload['params']=params
        headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json','Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2024-11-05'}
        if self.session:headers['Mcp-Session-Id']=self.session
        req=urllib.request.Request(self.url,data=json.dumps(payload).encode(),headers=headers,method='POST')
        with urllib.request.urlopen(req,timeout=self.timeout) as response:
            session=response.headers.get('Mcp-Session-Id')
            if session:self.session=session
            raw=response.read().decode()
        if notify:return None
        if raw.startswith('event:') or raw.startswith('data:'):
            messages=[json.loads(line[5:].strip()) for line in raw.splitlines() if line.startswith('data:')]
            result=next(x for x in messages if x.get('id')==self.n)
        else:result=json.loads(raw)
        if 'error' in result:raise RuntimeError(str(result['error']))
        return result['result']
    def tool(self,name,**args):
        r=self.call('tools/call',{'name':name,'arguments':args})
        if r.get('isError'):raise RuntimeError(str(r))
        return '\n'.join(c.get('text','') for c in r.get('content',[]))

def main():
    mode=sys.argv[1]
    root=pathlib.Path(sys.argv[2] if len(sys.argv)>2 else '/srv/wiki/stack')
    creds=json.loads((root/'credentials.json').read_text())
    url=os.environ.get('WIKI_MCP_URL','https://wiki.example.invalid/mcp') if mode in ['seed','backup'] else 'https://wiki.example.invalid/mcp'
    client=None
    tools=[]
    if mode!='backup' or '--export-markdown' in sys.argv:
        client=MCP(url,creds['mcp_token'])
        tools=client.call('tools/list')['tools']
    if mode in ['seed','verify']:
        run=uuid.uuid4().hex
        path='00-Test/server-mcp-'+run[:12]+'.md'
        body=f'---\nstatus: test\ncreated: 2026-10-12\n---\n# 服务连接验证\n\n验证编号：{run}\n\n服务器已能通过 MCP 新建和读取 Markdown。\n'
        assert 'saved' in client.tool('write_note',path=path,content=body).lower()
        assert run in client.tool('read_note',path=path)
        assert path in client.tool('search_notes',terms=[run])
        blocked=client.tool('write_note',path='99-System/should-not-exist.md',content='scope test')
        assert 'denied' in blocked.lower()
        try:
            urllib.request.urlopen(urllib.request.Request(url,data=b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}',headers={'Content-Type':'application/json','Accept':'application/json,text/event-stream'}),timeout=15)
            raise AssertionError('Anonymous request should be rejected')
        except urllib.error.HTTPError as e:assert e.code==401,e.code
        result={'run':run,'tools':[t['name'] for t in tools],'path':path,'write_read_search':'PASS','system_folder_write_denied':'PASS','anonymous_rejected':'PASS'}
        (root/('evidence-'+mode+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps(result,ensure_ascii=False))
    elif mode=='backup':
        stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        snapshot=root/'backups'/stamp
        snapshot.mkdir(parents=True,mode=0o700)
        auth=base64.b64encode((creds.get('admin_user','wiki-admin')+':'+creds['admin_password']).encode()).decode()
        def get_json(path):
            req=urllib.request.Request('http://localhost:15984/wiki/'+path,headers={'Authorization':'Basic '+auth})
            with urllib.request.urlopen(req,timeout=120) as response:return json.load(response)
        from backup_consistent import export_documents
        sequence,raw_count,paths,consistency=export_documents(get_json,snapshot)
        localdocs={}
        for localid in ['_local/obsidian_livesync_sync_parameters','_local/obsydian_livesync_milestone','_local/obsydian_livesync_nodeinfo']:
            localreq=urllib.request.Request('http://localhost:15984/wiki/'+localid,headers={'Authorization':'Basic '+auth})
            try:
                with urllib.request.urlopen(localreq,timeout=30) as response:localdocs[localid]=json.load(response)
            except urllib.error.HTTPError as e:
                if e.code!=404:raise
        if '_local/obsidian_livesync_sync_parameters' not in localdocs:raise RuntimeError('LiveSync encryption parameters missing; backup not accepted')
        (snapshot/'livesync-local-documents.json').write_text(json.dumps(localdocs,indent=2))
        securityreq=urllib.request.Request('http://localhost:15984/wiki/_security',headers={'Authorization':'Basic '+auth})
        with urllib.request.urlopen(securityreq,timeout=30) as response:(snapshot/'security.json').write_text(json.dumps(json.load(response),indent=2))
        (snapshot/'raw-manifest.json').write_text(json.dumps({'backup_version':3,'created':stamp,'couchdb_sequence':sequence,'raw_documents':raw_count,'raw_format':'gzip NDJSON; one document per line; includes attachments','local_documents':list(localdocs),'markdown_notes_expected':len(paths),'markdown_export':'in progress'},ensure_ascii=False,indent=2))
        if '--export-markdown' not in sys.argv:
            manifest={'backup_version':4,'created':stamp,'couchdb_sequence':sequence,'raw_documents':raw_count,'raw_format':'gzip NDJSON; one document per line; includes attachments','local_documents':list(localdocs),'markdown_notes_expected':len(paths),'markdown_export':'not requested; complete encrypted vault backed up without MCP bulk reads','status':'COMPLETE_ENCRYPTED_SNAPSHOT'}
            manifest.update(consistency)
            (snapshot/'raw-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
            (snapshot/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
            print(json.dumps({'snapshot':str(snapshot),'raw_documents':raw_count,'markdown_notes_expected':len(paths),'backup_version':4,'status':manifest['status']}))
            return
        manifest=[]
        for path in sorted(set(paths)):
            if pathlib.PurePosixPath(path).is_absolute() or '..' in pathlib.PurePosixPath(path).parts:raise RuntimeError('Unsafe vault path')
            result=client.tool('read_note',path=path)
            if '\n\n---\n\n' not in result:raise RuntimeError('Cannot export '+path)
            body=result.split('\n\n---\n\n',1)[1]
            target=snapshot/'markdown'/path
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_text(body,encoding='utf-8')
            manifest.append({'path':path,'sha256':hashlib.sha256(body.encode()).hexdigest()})
        (snapshot/'manifest.json').write_text(json.dumps({'backup_version':3,'created':stamp,'couchdb_sequence':sequence,'raw_documents':raw_count,'raw_format':'gzip NDJSON; one document per line; includes attachments','local_documents':list(localdocs),'notes':manifest},ensure_ascii=False,indent=2))
        if not manifest:raise RuntimeError('Empty export; snapshot not accepted')
        latest=root/'markdown-export'
        latest.mkdir(exist_ok=True)
        # Copy only: historical files are never silently removed.
        shutil.copytree(snapshot/'markdown',latest,dirs_exist_ok=True)
        print(json.dumps({'snapshot':str(snapshot),'markdown_notes':len(manifest),'raw_documents':raw_count,'backup_version':3}))

if __name__=='__main__':main()
