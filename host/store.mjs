import {mkdir,open,rename,readdir,readFile,unlink} from 'node:fs/promises';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';

export function jsonStore(root) {
  const path=key=>{
    if(typeof key!=='string'||!/^[-a-zA-Z0-9_.]{1,200}$/.test(key))throw Error('Invalid state key');
    return join(root,key+'.json');
  };
  const get=async key=>{try{return JSON.parse(await readFile(path(key),'utf8'));}catch(e){if(e.code==='ENOENT')return null;throw e;}};
  const set=async(key,value)=>{
    await mkdir(root,{recursive:true,mode:0o700});
    const target=path(key),temp=target+'.'+randomUUID()+'.tmp';
    try {
      const file=await open(temp,'wx',0o600);
      try{await file.writeFile(JSON.stringify(value));await file.sync();}finally{await file.close();}
      await rename(temp,target);
    }finally{await unlink(temp).catch(e=>{if(e.code!=='ENOENT')throw e;});}
  };
  const keys=async()=>{try{return(await readdir(root)).filter(x=>x.endsWith('.json')).map(x=>x.slice(0,-5));}catch(e){if(e.code==='ENOENT')return [];throw e;}};
  return {get,set,keys};
}
