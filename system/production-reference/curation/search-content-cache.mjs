/** Encrypted per-note search cache. Originals and CouchDB remain authoritative.
 * No plaintext cache, no extra worker, no parallel requests, no giant JSON snapshot.
 */
import {mkdir,readFile,writeFile,rename,chmod} from 'node:fs/promises';
import {createHash,randomBytes,scryptSync,createCipheriv,createDecipheriv} from 'node:crypto';
import {join} from 'node:path';
export function attachEncryptedContentCache(index,persistPath,passphrase){
 if(!persistPath||!passphrase)return {enabled:false,reason:'encrypted cache requires passphrase'};
 const folder=persistPath+'.bodies-v1';let key,hydrating=false,chain=Promise.resolve();const dirty=new Set();
 const original={load:index.loadFromDisk.bind(index),save:index.saveToDisk.bind(index),update:index.update.bind(index),remove:index.remove.bind(index)};
 const cachePath=p=>join(folder,createHash('sha256').update(p).digest('hex')+'.bin');
 async function initialize(){
  if(key)return;await mkdir(folder,{recursive:true,mode:0o700});await chmod(folder,0o700);
  const p=join(folder,'salt');let salt;
  try{salt=await readFile(p);}catch(e){if(e.code!=='ENOENT')throw e;salt=randomBytes(16);await writeFile(p,salt,{flag:'wx',mode:0o600});}
  if(salt.length!==16)throw Error('invalid cache salt');key=scryptSync(passphrase,salt,32);
 }
 function seal(p,data){const iv=randomBytes(12),cipher=createCipheriv('aes-256-gcm',key,iv);cipher.setAAD(Buffer.from(p));const encrypted=Buffer.concat([cipher.update(JSON.stringify(data)),cipher.final()]);return Buffer.concat([iv,cipher.getAuthTag(),encrypted]);}
 function open(p,data){if(data.length<28)throw Error('incomplete cache');const dec=createDecipheriv('aes-256-gcm',key,data.subarray(0,12));dec.setAAD(Buffer.from(p));dec.setAuthTag(data.subarray(12,28));return JSON.parse(Buffer.concat([dec.update(data.subarray(28)),dec.final()]).toString());}
 index.update=(...args)=>{original.update(...args);if(!hydrating)dirty.add(args[0]);};
 index.remove=(...args)=>{original.remove(...args);dirty.delete(args[0]);};
 index.loadFromDisk=async()=>{
  const loaded=await original.load();if(!loaded)return loaded;
  const started=performance.now();let hits=0,misses=0;
  try{await initialize();hydrating=true;
   for(const note of index.listWithMtime()){
    try{const data=open(note.path,await readFile(cachePath(note.path)));if(data.schema!==1||data.path!==note.path||data.mtime!==note.mtime||typeof data.content!=='string')throw Error('cache version mismatch');original.update(note.path,data.content,note.mtime);hits++;}
    catch{misses++;}
   }
  }catch{misses=index.size;}finally{hydrating=false;}
  // A partial load must not retain newer cached mtimes that can suppress
  // authoritative earlier records during a from-zero recovery.
  if(misses){index.content.clear();index.titles?.clear();}
  // Incomplete cache falls back to the existing authoritative from-zero replay.
  // A future startup can only skip that replay when every metadata entry has text.
  console.info('SEARCH_BODY_CACHE '+JSON.stringify({hits,misses,seconds:(performance.now()-started)/1000,complete:index.contentCount===index.size}));
  return loaded;
 };
 index.saveToDisk=()=>{
  const save=async()=>{
   try{
    await initialize();
    // Capture each version just before its write. If an update races an await,
    // the dirty set retains it and we flush again before advancing metadata.
    while(dirty.size){
     const p=dirty.values().next().value;dirty.delete(p);const content=index.getContent(p);if(content===null)continue;
     const mtime=index.mtimes.get(p),body=seal(p,{schema:1,path:p,mtime,content});
     const dest=cachePath(p),temp=dest+'.tmp';await writeFile(temp,body,{mode:0o600});await rename(temp,dest);
    }
    await original.save();
   }catch(error){
    // Do not advance persisted metadata past an incomplete cache checkpoint.
    // Existing metadata and source files stay intact; next startup can rebuild.
    console.warn('SEARCH_BODY_CACHE save failed; authoritative rebuild remains available: '+error.message);
   }
  };
  const next=chain.then(save,save);chain=next;return next;
 };
 async function validate(){
  await initialize();const missing=[];let hits=0;
  for(const note of index.listWithMtime()){
   try{const data=open(note.path,await readFile(cachePath(note.path)));if(data.schema!==1||data.path!==note.path||data.mtime!==note.mtime||typeof data.content!=='string')throw Error('cache version mismatch');hits++;}
   catch{missing.push(note.path);}
  }
  return {hits,missing};
 }
 return {enabled:true,folder,validate};
}
