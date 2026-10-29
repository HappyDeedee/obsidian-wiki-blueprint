// Private CLI used over SSH by the format worker. Never exposed as a public MCP tool.
import {readdirSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';
import {createInterface} from 'node:readline';
const digest=b=>createHash('sha256').update(b).digest('hex');
const testPrefix=process.env.KB_FORMAT_TEST_PREFIX;
if(testPrefix&&!/^00-Test\/v19-bh\/[0-9a-f]{12}\/$/.test(testPrefix))throw Error('Invalid isolated test prefix');
const valid=p=>typeof p==='string'&&(testPrefix?p.startsWith(testPrefix):p.startsWith('00-Inbox/'))&&!p.includes('\\')&&!/[\x00-\x1f:]/.test(p)&&p.split('/').every(s=>s&&!s.startsWith('.'));
const name=readdirSync('/app/dist').find(n=>/^vault-[A-Z0-9]+\.js$/.test(n));
if(!name)throw Error('Vault runtime unavailable');
const {Vault}=await import(pathToFileURL('/app/dist/'+name));
const vault=new Vault({couchdbUrl:process.env.COUCHDB_URL,couchdbUser:process.env.COUCHDB_USER,couchdbPassword:process.env.COUCHDB_PASSWORD,database:process.env.COUCHDB_DATABASE,passphrase:process.env.COUCHDB_PASSPHRASE,obfuscatePaths:process.env.COUCHDB_OBFUSCATE_PROPERTIES==='true',idDerivationKey:process.env.COUCHDB_ID_DERIVATION_KEY,caseSensitive:process.env.COUCHDB_CASE_SENSITIVE==='true'});
// Prevent the underlying library from mixing private path logs into the JSON response.
console.log=()=>{};console.warn=()=>{};
await vault.init();
async function handle(input){
 let result;
 const m=vault.manipulator;
 if(input.action==='list'){
  const entries=[];
  for await(const doc of m.enumerateAllNormalDocs({metaOnly:true})){
   const p=doc.path??'';
   if(!doc.deleted&&valid(p)&&!p.toLowerCase().endsWith('.md'))entries.push({path:p,size:doc.size,mtime:doc.mtime,type:doc.type,revision:doc._rev});
  }
  result={entries};
 }else if(input.action==='read'){
  if(!valid(input.path)||input.path.toLowerCase().endsWith('.md'))throw Error('Invalid attachment path');
  const meta=await m.get(input.path,true);
  if(!meta||meta.deleted)throw Error('Attachment missing');
  if(meta.size>25*1024*1024)throw Error('Attachment exceeds 25 MB conversion limit');
  const entry=await m.get(input.path);
  if(!entry||!Array.isArray(entry.data))throw Error('Attachment chunks unavailable');
  const {decodeBinary}=await import('octagonal-wheels/binary');
  const data=entry.type==='newnote'?Buffer.from(decodeBinary(entry.data)):Buffer.from(entry.data.join(''),'utf8');
  if(data.length>25*1024*1024)throw Error('Attachment exceeds limit');
  const after=await m.get(input.path,true);
  if(!after||after.deleted||after._rev!==meta._rev||after.mtime!==meta.mtime)throw Error('Attachment changed during read');
  result={path:input.path,sha256:digest(data),size:data.length,mtime:meta.mtime,data:data.toString('base64')};
 }else if(input.action==='transcript_status'){
  if(typeof input.path!=='string'||!input.path.endsWith('.md')||!(testPrefix?input.path.startsWith(testPrefix):/^(00-Test\/会议\/|00-Inbox\/会议\/|00-Inbox\/已提取正文\/)/.test(input.path))||input.path.includes('..')||input.path.includes('\\'))throw Error('Invalid transcript status path');
  const text=await vault.readNote(input.path);
  result={exists:typeof text==='string'&&text.trim().length>0};
 }else if(input.action==='write'||input.action==='report'){
  const report=input.action==='report'&&input.path===(testPrefix?testPrefix+'转换状态.md':'90-Logs/附件转换/当前状态.md');
  if((!report&&(!valid(input.path)||!input.path.startsWith(testPrefix?testPrefix+'已提取正文/':'00-Inbox/已提取正文/')||!input.path.endsWith('.md')))||typeof input.content!=='string')throw Error('Invalid conversion destination');
  const old=await vault.readNote(input.path);
  if(!report&&old!==null&&old!==input.content)throw Error('Conversion destination exists; preserved');
  if(old!==input.content&&!await vault.writeNote(input.path,input.content))throw Error('Write failed');
  if(await vault.readNote(input.path)!==input.content)throw Error('Readback failed');
  result={status:'PASS',sha256:digest(input.content),reused:old!==null};
 }else throw Error('Unknown bridge action');
 return result;
}
// One connection per worker scan, reused for listing, reads and writes.
for await(const line of createInterface({input:process.stdin,crlfDelay:Infinity})){
 try{process.stdout.write(JSON.stringify(await handle(JSON.parse(line)))+'\n');}
 catch(error){process.stdout.write(JSON.stringify({error:String(error.message)})+'\n');}
}
// All writes are awaited and read back; the library close hook can hang in a CLI.
process.stdout.write('',()=>process.exit(0));
