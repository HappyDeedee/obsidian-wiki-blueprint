/** Only this adapter knows the pinned upstream bundle layout. Workers use this public interface. */
import {readdir} from 'node:fs/promises';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
import {normalizedPath} from '../system/production-reference/curation/mcp-role-policy.mjs';

export async function upstreamModule(vault,name){
  if(!['octagonal-wheels/binary','octagonal-wheels/encryption/encryption'].includes(name))throw Error('Unapproved adapter dependency');
  // ESM-only exports: use the same pinned public package, not CJS resolution.
  return import(name);
}
export function checkedPath(path){
  normalizedPath(path);
  if(path.split('/').some(x=>x.startsWith('.')))throw Error('Hidden vault path denied');
  return path;
}
export async function openVault(config) {
  const files=(await readdir(config.paths.upstream_dist)).filter(x=>/^vault-[A-Z0-9]+\.js$/.test(x));
  if(files.length!==1)throw Error('Pinned upstream Vault export unavailable or ambiguous');
  const {Vault}=await import(pathToFileURL(join(config.paths.upstream_dist,files[0])).href);
  const c=config.couchdb;
  const vault=new Vault({couchdbUrl:c.url,couchdbUser:c.user,couchdbPassword:c.password,database:c.database,
    passphrase:c.passphrase,obfuscatePaths:c.obfuscate_paths===true,idDerivationKey:c.id_derivation_key,
    caseSensitive:c.case_sensitive===true});
  // Upstream path logs must not become public service logs.
  const log=console.log,warn=console.warn;
  try{console.log=()=>{};console.warn=()=>{};await vault.init();}finally{console.log=log;console.warn=warn;}
  return vault;
}
export async function listAttachments(vault,prefix='00-Inbox/') {
  checkedPath(prefix.replace(/\/$/,''));const rows=[];
  for await(const doc of vault.manipulator.enumerateAllNormalDocs({metaOnly:true})) {
    if(doc.deleted||!doc.path?.startsWith(prefix)||doc.path.endsWith('.md'))continue;
    checkedPath(doc.path);rows.push({path:doc.path,size:doc.size,mtime:doc.mtime,revision:doc._rev});
  }
  return rows;
}
export async function readAttachment(vault,path,maximum=25*1024*1024) {
  checkedPath(path);
  if(!/^(00-Inbox|00-Test|10-Sources)\//.test(path)||path.endsWith('.md'))throw Error('Attachment scope denied');
  const before=await vault.manipulator.get(path,true);
  if(!before||before.deleted||!(before.size>0&&before.size<=maximum))throw Error('Attachment unavailable or over limit');
  const full=await vault.manipulator.get(path);
  if(!full||!Array.isArray(full.data))throw Error('Attachment chunks unavailable');
  const {decodeBinary}=await upstreamModule(vault,'octagonal-wheels/binary');
  const bytes=full.type==='newnote'?Buffer.from(decodeBinary(full.data)):Buffer.from(full.data.join(''),'utf8');
  const after=await vault.manipulator.get(path,true);
  if(!after||after.deleted||before._rev!==full._rev||before._rev!==after._rev||bytes.length!==before.size||bytes.length>maximum)throw Error('Attachment changed or incomplete');
  return {path,size:bytes.length,mtime:before.mtime,sha256:createHash('sha256').update(bytes).digest('hex'),data:bytes.toString('base64')};
}
