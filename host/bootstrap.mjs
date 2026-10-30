/** Seed ONLY missing starter pages in the configured new installation. */
import {readdir,readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {pathToFileURL,fileURLToPath} from 'node:url';
import {loadConfig} from './config.mjs';
import {openVault,checkedPath} from './adapter.mjs';

export async function bootstrap(vault){
  const template=fileURLToPath(new URL('../vault-template/',import.meta.url));
  const entries=[];
  async function visit(folder,prefix=''){
    for(const ent of await readdir(folder,{withFileTypes:true})){
      if(ent.isSymbolicLink())throw Error('Template symlink denied');
      if(ent.isDirectory())await visit(join(folder,ent.name),prefix+ent.name+'/');
      else if(ent.isFile()&&ent.name.endsWith('.md'))entries.push([prefix+ent.name,await readFile(join(folder,ent.name),'utf8')]);
    }
  }
  await visit(template);
  entries.push(['20-Wiki/综合/已确认词典.md',await readFile(new URL('../system/config/confirmed-dictionary.empty.md',import.meta.url),'utf8')]);
  entries.push(['90-Logs/入库台账.md','# 入库台账\n']);
  let created=0,preserved=0;
  for(const [path,content]of entries){
    checkedPath(path);
    if(await vault.readNote(path)!==null){preserved++;continue;}
    if(!await vault.writeNote(path,content)||await vault.readNote(path)!==content)throw Error('Starter page readback failed');
    created++;
  }
  return {status:'PASS',created,preserved,scope:'Missing starter pages only; existing pages never overwritten'};
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){
  const vault=await openVault(await loadConfig());
  try{console.log(JSON.stringify(await bootstrap(vault)));}finally{await vault.close();}
}
