"""Generate YOUR private sync settings; never read the author's environment.

Starts no services. Run the explicitly documented Docker command afterwards.
"""
from pathlib import Path
import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
from urllib.parse import urlparse, quote
import urllib.request
from wiki import WikiError, save_json
from system import home_path


def init(directory, public_url='https://wiki.example.invalid', vault_name='MyWiki'):
    root = home_path(directory)
    if root.exists() and any(root.iterdir()):
        raise WikiError('Use a NEW empty settings directory; existing secrets are never replaced')
    u = urlparse(public_url)
    if u.scheme != 'https' or not u.hostname or u.username or u.password or u.query or u.fragment or u.path not in {'','/'}:
        raise WikiError('Use your HTTPS root URL without credentials, query or path')
    if not vault_name.strip() or any(c in vault_name for c in '\r\n"\x00'):
        raise WikiError('Invalid vault name')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    cfg = {'COUCHDB_ADMIN_USER':'wiki-admin','COUCHDB_ADMIN_PASSWORD':secrets.token_urlsafe(32),
           'LIVESYNC_USER':'wiki-sync','LIVESYNC_PASSWORD':secrets.token_urlsafe(32),
           'COUCHDB_DATABASE':'wiki','COUCHDB_PASSPHRASE':secrets.token_urlsafe(40),
           'MCP_READER_TOKEN':secrets.token_urlsafe(32),'MCP_PUBLIC_URL':public_url.rstrip('/')+'/mcp',
           'SYNC_PUBLIC_URL':public_url.rstrip('/')+'/sync','VAULT_NAME':vault_name,
           'TZ':'Asia/Shanghai','COUCHDB_LOCAL_PORT':'15984','MCP_LOCAL_PORT':'18787'}
    # JSON string quoting is compatible with these generated Compose env values.
    env = root/'stack.env'
    env.write_text('\n'.join(k+'='+json.dumps(v,ensure_ascii=False) for k,v in cfg.items())+'\n',encoding='utf-8',newline='\n')
    save_json(root/'settings.private.json',cfg)
    hashes=[]
    for role in ['read-only','proposal','curator','meeting']:
        token=cfg['MCP_READER_TOKEN'] if role=='read-only' else secrets.token_urlsafe(32)
        save_json(root/(role+'.private.json'),{'role':role,'token':token})
        hashes.append({'id':role,'role':role,'enabled':True,
                       'sha256':hashlib.sha256(('Bearer '+token).encode()).hexdigest(),
                       **({'tools':['import_meeting'],'pathPrefixes':['00-Inbox/会议/']} if role=='meeting' else {})})
    save_json(root/'mcp-identities.private.json',hashes)
    for p in root.iterdir():
        try: os.chmod(p,0o600)
        except OSError:pass
    return {'status':'GENERATED','directory':str(root),'services_started':False,
            'next':'Set YOUR domain, then follow system/runbooks/02-sync.md',
            'role_registry_installed':False,'stock_mcp_profile':'read-only only'}


def init_db(directory):
    cfg=json.loads((Path(directory)/'settings.private.json').read_text(encoding='utf-8'))
    base='http://localhost:'+cfg['COUCHDB_LOCAL_PORT']
    authorization='Basic '+base64.b64encode((cfg['COUCHDB_ADMIN_USER']+':'+cfg['COUCHDB_ADMIN_PASSWORD']).encode()).decode()
    def request(method,path,value=None):
        data=None if value is None else json.dumps(value).encode()
        req=urllib.request.Request(base+path,data=data,method=method,headers={'Authorization':authorization,'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=15) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (404,409,412):return e.code,json.load(e)
            raise WikiError('CouchDB request failed with status '+str(e.code))
    request('GET','/_up')
    db=quote(cfg['COUCHDB_DATABASE'],safe='')
    status,existing=request('GET','/'+db)
    if status!=404:
        raise WikiError('Database already exists; inspect it. This initializer NEVER changes existing access or users')
    user_path='/_users/'+quote('org.couchdb.user:'+cfg['LIVESYNC_USER'],safe='')
    status,_=request('GET',user_path)
    if status!=404:raise WikiError('Sync user already exists; refusing to replace its password')
    for name in ['_users','_replicator','_global_changes']:
        request('PUT','/'+name)
    user={'_id':'org.couchdb.user:'+cfg['LIVESYNC_USER'],'name':cfg['LIVESYNC_USER'],
          'type':'user','roles':[],'password':cfg['LIVESYNC_PASSWORD']}
    status,_=request('PUT',user_path,user)
    if status not in (201,202):raise WikiError('User creation failed; inspect before retrying')
    status,_=request('PUT','/'+db)
    if status not in (201,202):raise WikiError('Database creation failed; inspect before retrying')
    sec={'admins':{'names':[cfg['COUCHDB_ADMIN_USER']],'roles':[]},
         'members':{'names':[cfg['LIVESYNC_USER']],'roles':[]}}
    status,_=request('PUT','/'+db+'/_security',sec)
    if status not in (200,201,202):raise WikiError('Database security setup failed; do not expose the port')
    return {'status':'DATABASE_CREATED','database':cfg['COUCHDB_DATABASE'],'credentials_printed':False,
            'next':'Validate anonymous rejection and authorized replication before connecting real devices'}


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    x=sub.add_parser('init');x.add_argument('--directory',required=True);x.add_argument('--public-url',default='https://wiki.example.invalid');x.add_argument('--vault-name',default='MyWiki')
    x=sub.add_parser('init-db');x.add_argument('--directory',required=True)
    a=p.parse_args()
    try:
        r=init(a.directory,a.public_url,a.vault_name)if a.command=='init'else init_db(a.directory)
        print(json.dumps(r,ensure_ascii=False,indent=2))
    except (WikiError,OSError,ValueError,KeyError)as e:
        print(json.dumps({'status':'ERROR','reason':str(e)},ensure_ascii=False));raise SystemExit(1)
