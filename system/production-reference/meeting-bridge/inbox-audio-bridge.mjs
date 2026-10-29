// Native LiveSync reader. JSON requests and bounded binary responses over stdio.
import fs from 'node:fs';
import {openAsBlob,createReadStream} from 'node:fs';
import { createHash } from 'node:crypto';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';

const PREFIX = '00-Inbox/会议/待处理/';
export function validArchivePath(path) {
  if (typeof path!=='string' || /[\\\x00\r\n\[\]|#^]/.test(path)) return false;
  const match=/^(?:00-Inbox|10-Sources|00-Test)\/会议\/([^/]+)\/([^/]+)\.(m4a|mp3|wav|aac|caf|mp4)$/.exec(path);
  return !!match && /^\d{2}-\d{2}-\d{2} /.test(match[1]) && (match[2]==='录音' || match[1]===match[2]);
}
export async function verifyStoredAudio(vault,path,expected,size,decode) {
  try {
    const doc=await vault.manipulator.get(path);
    if (!doc || doc.deleted) return null;
    if (doc.type!=='newnote' || !Array.isArray(doc.data)) throw Error('audio_changed_preserved');
    const hash=createHash('sha256');let bytes=0;
    if (doc.data[0]?.startsWith('%')) {
      const data=Buffer.from(decode(doc.data));hash.update(data);bytes=data.length;
    } else for(const chunk of doc.data) {
      const data=Buffer.from(chunk,'base64');hash.update(data);bytes+=data.length;
    }
    if (bytes!==size || hash.digest('hex')!==expected) throw Error('audio_changed_preserved');
    return {revision:doc._rev};
  }finally {vault.manipulator.liveSyncLocalDB.clearCaches();}
}
export function validAudioPath(path) {
  if (typeof path !== 'string' || !path.startsWith(PREFIX)) return false;
  const name = path.slice(PREFIX.length);
  return !!name && !/[\\/\x00\r\n\[\]|#^]/.test(name) && /\.(m4a|mp3|wav|aac|caf|mp4)$/i.test(name);
}
export function checkedBinary(before, full, after, decode, maximum) {
  if (!before || before.deleted || !after || after.deleted || !full || full.type !== 'newnote' || !Array.isArray(full.data)) throw Error('audio_not_ready');
  if (before._rev !== after._rev || before._rev !== full._rev || before.size !== after.size) throw Error('audio_changed_during_read');
  if (!(before.size > 0 && before.size <= maximum)) throw Error('audio_over_listener_limit');
  const bytes = Buffer.from(decode(full.data));
  if (bytes.length !== before.size || bytes.length !== full.size) throw Error('audio_incomplete');
  return bytes;
}
export function sampledIdFormat(rows) {
  const ids=rows.filter(row=>row.doc && !row.doc._deleted && ['plain','newnote'].includes(row.doc.type))
    .slice(0,20).map(row=>row.id);
  if (!ids.length) return null;
  const count=ids.filter(id=>id.startsWith('f:')).length;
  return count===0?'plain':count===ids.length?'obfuscated':'mixed';
}
export function createBridge(vault, decode, decryptPath, maximum) {
  const metadata = doc => ({path:doc.path, revision:doc._rev, size:doc.size, mtime:doc.mtime, deleted:!!(doc.deleted || doc._deleted)});
  return async request => {
    const local = vault.manipulator.liveSyncLocalDB;
    if (['verify_archive','save_archive'].includes(request.op)) {
      if (!(validArchivePath(request.path) || (request.op==='verify_archive' && validAudioPath(request.path))) ||
          !/^[a-f0-9]{64}$/.test(request.sha256) || !(request.size>0 && request.size<=maximum)) throw Error('invalid_archive_request');
      const existing=await verifyStoredAudio(vault,request.path,request.sha256,request.size,decode);
      if(request.op==='verify_archive' || existing)return {entry:existing};
      const extension=request.path.split('.').at(-1),file='/app/meeting-data/'+request.sha256+'.'+extension;
      if(fs.statSync(file).size!==request.size)throw Error('audio_archive_size_mismatch');
      const hash=createHash('sha256');for await(const block of createReadStream(file))hash.update(block);
      if(hash.digest('hex')!==request.sha256)throw Error('audio_archive_hash_mismatch');
      if(!await vault.manipulator.put(request.path,await openAsBlob(file),{ctime:Date.now(),mtime:Date.now(),size:request.size}))throw Error('audio_archive_write_failed');
      local.clearCaches();global.gc?.();
      const saved=await verifyStoredAudio(vault,request.path,request.sha256,request.size,decode);
      if(!saved)throw Error('audio_archive_readback_failed');
      return {entry:saved};
    }
    if (request.op === 'head') return {since:String((await local.localDatabase.info()).update_seq)};
    if (request.op === 'scan') {
      const batch = await local.localDatabase.changes({since:request.since, include_docs:true, live:false, limit:100,
        selector:{type:{$in:['plain','newnote']}}});
      const entries = [];
      for (const change of batch.results) {
        if (!change.doc || !['plain','newnote'].includes(change.doc.type)) continue;
        const doc = {...change.doc, path:await decryptPath(change.doc.path ?? '')};
        if (validAudioPath(doc.path)) entries.push(metadata(doc));
      }
      local.clearCaches();
      return {since:String(batch.last_seq), scanned:batch.results.length, entries};
    }
    if (!validAudioPath(request.path)) throw Error('invalid_source_audio_path');
    local.clearCaches();
    const before = await vault.manipulator.get(request.path, true);
    if (request.op === 'meta') return {entry:before && !before.deleted ? metadata({...before,path:request.path}) : null};
    if (request.op !== 'read') throw Error('invalid_operation');
    if (!before || before.deleted) throw Error('audio_not_ready');
    if (before._rev !== request.revision) throw Error('audio_changed_during_read');
    if (!(before.size > 0 && before.size <= maximum)) throw Error('audio_over_listener_limit');
    const full = await vault.manipulator.get(request.path);
    local.clearCaches();
    const after = await vault.manipulator.get(request.path, true);
    const binary = checkedBinary(before, full, after, decode, maximum);
    local.clearCaches();
    return {binary, path:request.path, revision:before._rev, size:binary.length,
      sha256:createHash('sha256').update(binary).digest('hex')};
  };
}

async function main() {
  console.log=()=>{}; console.warn=()=>{}; console.info=()=>{}; console.error=()=>{};
  const trace=message=>{if(process.env.MEETING_BRIDGE_TRACE==='1')process.stderr.write(message+'\n');};
  trace('loading_vault_module');
  const moduleName=fs.readdirSync('/app/dist').filter(n=>/^vault-.*\.js$/.test(n)).find(n=>{
    const source=fs.readFileSync('/app/dist/'+n,'utf8');
    return source.includes('async listNotesWithMtime(') && source.includes('DirectFileManipulator');
  });
  if (!moduleName) throw Error('vault_module_missing');
  const {Vault}=await import('/app/dist/'+moduleName);
  trace('vault_module_loaded');
  const {decodeBinary}=await import('octagonal-wheels/binary');
  const {isPathProbablyObfuscated,decrypt}=await import('octagonal-wheels/encryption/encryption');
  const vault=new Vault({couchdbUrl:process.env.COUCHDB_URL,couchdbUser:process.env.COUCHDB_USER||'admin',
    couchdbPassword:process.env.COUCHDB_PASSWORD,database:process.env.COUCHDB_DATABASE||'obsidian',
    passphrase:process.env.COUCHDB_PASSPHRASE,obfuscatePaths:process.env.COUCHDB_OBFUSCATE_PROPERTIES==='true',
    idDerivationKey:process.env.COUCHDB_ID_DERIVATION_KEY,caseSensitive:process.env.COUCHDB_CASE_SENSITIVE==='true'});
  // Same file-ID classification as Vault, using the current-key index rather than replaying
  // every historical change. Native readiness/key validation and reconciliation still run.
  const originalDetect=vault.detectIdFormat.bind(vault);
  vault.detectIdFormat=async()=>{
    const sample=await vault.manipulator.liveSyncLocalDB.localDatabase.allDocs({startkey:'',endkey:'h:',limit:100,include_docs:true});
    return sampledIdFormat(sample.rows) || await originalDetect();
  };
  if (process.env.MEETING_BRIDGE_TRACE==='1') for (const method of ['awaitManipulatorReady','reconcileObfuscation','detectIdFormat']) {
    const original=vault[method].bind(vault);
    vault[method]=async(...args)=>{trace(method+'_start');const value=await original(...args);trace(method+'_done');return value;};
  }
  trace('vault_initialise_start');
  const initialisationWatchdog=setTimeout(()=>process.exit(2),125000);
  try { await vault.init(); } finally { clearTimeout(initialisationWatchdog); }
  trace('vault_initialise_done');
  const handle=createBridge(vault,decodeBinary,async p=>isPathProbablyObfuscated(p)?await decrypt(p,process.env.COUCHDB_PASSPHRASE,false):p,
    Number(process.env.MEETING_AUDIO_MAX_BYTES || 64*1024*1024));
  process.stdout.write(JSON.stringify({ok:true,ready:true})+'\n');
  for await (const line of createInterface({input:process.stdin,crlfDelay:Infinity})) {
    const watchdog=setTimeout(()=>process.exit(2),125000);
    try {
      const result=await handle(JSON.parse(line));
      const {binary,...header}=result;
      await new Promise((resolve,reject)=>process.stdout.write(JSON.stringify({ok:true,...header})+'\n',e=>e?reject(e):resolve()));
      if (binary) await new Promise((resolve,reject)=>process.stdout.write(binary,e=>e?reject(e):resolve()));
    } catch (error) {
      const code=/^(audio_|invalid_)[a-z_]+$/.test(error.message)?error.message:'vault_read_retry';
      process.stdout.write(JSON.stringify({ok:false,error:code})+'\n');
    } finally { clearTimeout(watchdog); global.gc?.(); }
  }
  await vault.close();
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().then(()=>process.exit(0),()=>{process.stdout.write('{"ok":false,"error":"bridge_initialisation_failed"}\n');process.exit(1);});
}
