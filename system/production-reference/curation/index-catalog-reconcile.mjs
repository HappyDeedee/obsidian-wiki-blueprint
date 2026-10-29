/** Reconcile the search catalog with authoritative note metadata after catch-up.
 * A transient body-read failure must not silently remove an existing note.
 * This updates only the disposable search index; it never writes vault notes.
 */
export async function reconcileIndexCatalog({vault,index,seen}) {
 const catalog=await vault.listNotesWithMtime();
 const indexed=new Set(index.listPaths()),unreadable=[];let repaired=0;
 for(const note of catalog){
  if(seen.has(note.path)&&indexed.has(note.path))continue;
  let content=null;
  for(let attempt=0;attempt<2&&content===null;attempt++){
   try{content=await vault.readNote(note.path);}catch{content=null;}
  }
  if(typeof content!=='string'){unreadable.push(note.path);continue;}
  index.update(note.path,content,note.mtime);seen.add(note.path);repaired++;
 }
 if(unreadable.length)throw Error('Search catalog incomplete: '+unreadable.length+' authoritative notes could not be read; ghost pruning skipped. First: '+unreadable.slice(0,3).join(', '));
 return {catalog:catalog.length,repaired};
}
