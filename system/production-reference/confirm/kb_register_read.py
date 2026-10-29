"""Bounded read-only registry view. Validate decrypted cache against the live MCP view."""
import datetime,json,re,subprocess
PATH='90-Logs/待确认/问题登记.md'
NODE=r'''import{readFileSync,readdirSync,existsSync}from'node:fs';import{createHash,scryptSync,createDecipheriv}from'node:crypto';
const paths=readdirSync('/data').filter(p=>/^[a-f0-9]{12}$/.test(p)).map(p=>'/data/'+p+'/search-index.json.bodies-v1').filter(p=>existsSync(p+'/salt'));if(paths.length!==1)throw Error('Cache ambiguity');
const dir=paths[0],p='90-Logs/待确认/问题登记.md',b=readFileSync(dir+'/'+createHash('sha256').update(p).digest('hex')+'.bin');if(b.length>12*1024**2)throw Error('Registry size limit');
const key=scryptSync(process.env.COUCHDB_PASSPHRASE,readFileSync(dir+'/salt'),32),d=createDecipheriv('aes-256-gcm',key,b.subarray(0,12));d.setAAD(Buffer.from(p));d.setAuthTag(b.subarray(12,28));const v=JSON.parse(Buffer.concat([d.update(b.subarray(28)),d.final()]).toString());if(v.path!==p)throw Error('Cache path mismatch');process.stdout.write(JSON.stringify(v));'''
def read_register(c):
 before=c.tool('get_note_metadata',{'path':PATH})
 p=subprocess.run(['/usr/bin/docker','exec','-i','obsidian-mcp','node','--max-old-space-size=192','--input-type=module','-'],input=NODE.encode(),capture_output=True,timeout=25)
 if p.returncode:raise RuntimeError('Registry read-only cache failed: '+p.stderr.decode(errors='replace')[-200:])
 if len(p.stdout)>16*1024**2:raise RuntimeError('Registry response exceeds limit')
 v=json.loads(p.stdout);text=v['content'];size=int(re.search(r'^Size: (\d+) bytes$',before,re.M)[1]);mtime=datetime.datetime.fromisoformat(re.search(r'^Modified: (.+)$',before,re.M)[1].replace('Z','+00:00')).timestamp()*1000
 if len(text.encode())!=size or v['mtime']<mtime:raise RuntimeError('Registry cache stale; do not generate cards')
 for offset in (0,max(0,len(text)-4000)):
  part=c.tool('read_note',{'path':PATH,'offset':offset,'limit':4000});m=re.search(r'\[Segment offset=(\d+) next_offset=(END|\d+) total_chars=(\d+)\]\n',part)
  if not m or int(m[1])!=offset or int(m[3])!=len(text):raise RuntimeError('Registry coverage receipt mismatch')
  end=len(text)if m[2]=='END'else int(m[2])
  if part[m.end():]!=text[offset:end]:raise RuntimeError('Registry live/cache mismatch')
 if before!=c.tool('get_note_metadata',{'path':PATH}):raise RuntimeError('Registry changed while checking')
 return text
