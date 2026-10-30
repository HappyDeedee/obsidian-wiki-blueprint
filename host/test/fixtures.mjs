import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash,randomBytes} from 'node:crypto';
import {once} from 'node:events';
import {createRuntime} from '../runtime.mjs';
import {serve} from '../server.mjs';
import {jsonStore} from '../store.mjs';

const hash=t=>createHash('sha256').update(t).digest('hex');
class MemoryVault {
  notes=new Map([
    ['99-System/知识库规则.md','# Public fictional rules\n'],
    ['20-Wiki/知识索引.md','# 知识索引\n'],['20-Wiki/总览.md','# 总览\n'],
    ['90-Logs/入库台账.md','# 入库台账\n']
  ]);
  async readNote(p){return this.notes.get(p)??null;}
  async writeNote(p,t){this.notes.set(p,t);return true;}
  async getMetadata(p){return this.notes.has(p)?{path:p,mtime:1577836800000}:null;}
  async listNotes(prefix){return [...this.notes.keys()].filter(p=>!prefix||p.startsWith(prefix)).sort();}
}
export async function fixture(t){
  const root=await mkdtemp(join(tmpdir(),'wiki-host-'));t.after(()=>rm(root,{recursive:true,force:true}));
  const config={paths:{state:root,registry:join(root,'registry.json')},http:{bind:'127.0.0.1',port:0}};
  const tokens={},rows=[];
  for(const role of ['read-only','proposal','curator','meeting']){
    tokens[role]=randomBytes(32).toString('hex');rows.push({id:role,role,enabled:true,sha256:hash('Bearer '+tokens[role]),tools:role==='meeting'?['import_meeting']:[]});
  }
  await writeFile(config.paths.registry,JSON.stringify(rows));
  const vault=new MemoryVault(),runtime=await createRuntime(config,vault,{importMeeting:async id=>({saved:true,job_id:id,test_double:true})});
  vault.notes.set('20-Wiki/综合/已确认词典.md',await readFile(new URL('../../system/config/confirmed-dictionary.empty.md',import.meta.url),'utf8'));
  const identity=role=>runtime.identity('Bearer '+tokens[role]);
  return {root,config,tokens,rows,vault,runtime,identity};
}
