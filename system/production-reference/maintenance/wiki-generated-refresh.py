"""Refresh derived files after the existing structure job, including failed jobs."""
import os
import pathlib,importlib.util,json,subprocess,datetime,signal,sys
ROOT=pathlib.Path(__file__).resolve().parent
STATE=ROOT/'generated-refresh'
def interrupted(*_):raise RuntimeError('Generated refresh interrupted')
signal.signal(signal.SIGTERM,interrupted)
s=importlib.util.spec_from_file_location('vc',ROOT/'vault-client.py');vc=importlib.util.module_from_spec(s);s.loader.exec_module(vc)
cfg=json.loads((ROOT/'credentials.json').read_text());c=vc.MCP(os.environ.get('WIKI_MCP_URL','https://wiki.example.invalid/mcp'),cfg['mcp_token'])
lease=json.loads(c.tool('curation_writer_acquire',owner='鐜版湁缁撴瀯浠诲姟鍚庡埛鏂版淳鐢熼槦鍒楀拰鍏ュ彛'))
evidence={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':lease['status'],'reports':[]}
if lease['status']=='ACQUIRED':
    held=STATE/'held.json';held.write_text(json.dumps(lease));held.chmod(0o600)
    try:
        config=STATE/'items.json'
        enabled=json.loads(config.read_text())if config.exists()else[]
        # Alias generation runs in a separate bounded worker.  A slow derived
        # view must not hold up the daily queue refresh or mark it failed.
        commands={'G':['wiki-pending-queue.py']}
        # Project entrance is published through the approved Windows channel.
        # Missing config never activates a rolled-back item.
        for args in [commands[k]for k in ['G']if k in enabled]:
            p=subprocess.run([sys.executable,str(ROOT/args[0]),*args[1:],'--publish','--held-lease',str(held),'--runtime-dir',str(STATE)],capture_output=True,text=True,timeout=150)
            evidence['reports'].append({'program':args,'code':p.returncode,'result':p.stdout[-2500:],'error':p.stderr[-1200:]})
        evidence['status']='PASS'if all(x['code']==0 for x in evidence['reports'])else'FAILED'
    finally:c.tool('curation_writer_release',token=lease['token'])
# The status page is a best-effort daily derived view. Its failure cannot
# change the structure job result, and it never runs in the attachment loop.
try:
    status_run=subprocess.run([sys.executable,str(ROOT/'format/inbox-status.py')],capture_output=True,text=True,timeout=600)
    evidence['inbox_status']={'code':status_run.returncode,'result':status_run.stdout[-4000:],'error':status_run.stderr[-1200:]}
except Exception as error:
    evidence['inbox_status']={'status':'FAILED','error':str(error)}
path=STATE/'last.json';path.write_text(json.dumps(evidence,ensure_ascii=False,indent=2));path.chmod(0o600)
print(json.dumps(evidence,ensure_ascii=False))
if evidence['status']=='FAILED':raise SystemExit(1)
