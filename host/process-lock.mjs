/** Fail closed rather than let two hosts race one private journal directory. */
import {mkdir,open,unlink} from 'node:fs/promises';
import {join} from 'node:path';
export async function acquireProcessLock(directory){
  await mkdir(directory,{recursive:true,mode:0o700});
  const path=join(directory,'host.lock');let file;
  try{file=await open(path,'wx',0o600);}catch(e){
    if(e.code==='EEXIST')throw Error('Host lock exists. Confirm the old process is stopped before manually removing this private lock.');
    throw e;
  }
  await file.writeFile(JSON.stringify({pid:process.pid,started:new Date().toISOString()})+'\n');await file.sync();
  return async()=>{await file.close();await unlink(path);};
}
