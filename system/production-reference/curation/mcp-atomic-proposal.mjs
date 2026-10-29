// LiveSync uses a live-base put with no revision: CouchDB rejects an existing ID.
// Ordinary writeNote() deliberately follows the latest revision and is NOT create-only.
import { mkdir,writeFile } from 'node:fs/promises';
import { dirname } from 'node:path';
export async function createProposal(vault,path,content) {
  const m=vault.manipulator;
  if(!m && typeof vault.safePath==='function') {
    const full=await vault.safePath(path,true);await mkdir(dirname(full),{recursive:true});
    await writeFile(full,content,{encoding:'utf8',flag:'wx'});return {ok:true};
  }
  if(!m?.liveSyncLocalDB?.putDBEntryWithLiveBaseRevision)throw Error('Atomic create backend unavailable');
  const now=Date.now(),blob=new Blob([content],{type:'text/plain'});
  const note={_id:await m.path2id(path),path,data:blob,ctime:now,mtime:now,size:Buffer.byteLength(content),type:'plain',datatype:'plain',eden:{},children:[]};
  const result=await m.liveSyncLocalDB.putDBEntryWithLiveBaseRevision(note,undefined,false);
  if(!result)throw Error('Atomic create failed');
  return result;
}
