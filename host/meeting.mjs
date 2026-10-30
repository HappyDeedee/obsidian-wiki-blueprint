/** Public importer: same production renderer, configured input, no source deletion. */
import {openAsBlob} from 'node:fs';
import {readFile as read,lstat as info} from 'node:fs/promises';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {renderMeeting} from '../system/production-reference/meeting-bridge/meeting-import.mjs';
import {readAttachment,checkedPath} from './adapter.mjs';

const hash=b=>createHash('sha256').update(b).digest('hex');
export function meetingImporter(config,vault) {
  let tail=Promise.resolve();
  const json=async path=>{
    const meta=await info(path);
    if(!meta.isFile()||meta.isSymbolicLink()||meta.size>8*1024*1024)throw Error('Meeting result exceeds JSON limit');
    return JSON.parse(await read(path,'utf8'));
  };
  async function run(job_id) {
    if(!/^[a-f0-9]{64}$/.test(job_id))throw Error('Invalid job ID');
    const folder=config.paths.meeting_data;
    const manifest=await json(join(folder,job_id+'.export.json'));
    const result=await json(join(folder,job_id+'.result.json'));
    if(manifest.job_id!==job_id||result.job_id!==job_id||manifest.task_id!==result.task_id||
      !['wav','m4a','mp3','aac','caf','mp4'].includes(manifest.extension)||
      !Array.isArray(result.paragraphs)||!Array.isArray(result.actions)||
      !Number.isInteger(manifest.bytes)||manifest.bytes<1||manifest.bytes>128*1024*1024)throw Error('Invalid meeting manifest');
    const audio=join(folder,job_id+'.'+manifest.extension),meta=await info(audio);
    if(!meta.isFile()||meta.isSymbolicLink()||meta.size!==manifest.bytes)throw Error('Meeting audio size/type mismatch');
    const bytes=await read(audio);
    if(bytes.length!==manifest.bytes||hash(bytes)!==job_id)throw Error('Meeting audio hash mismatch');
    let rendered;
    for(let n=1;n<=999;n++){
      const candidate=renderMeeting({...manifest,layout_suffix:n===1?'':String(n)},result);
      const texts=await Promise.all([candidate.note_path,candidate.transcript_path].filter(Boolean).map(p=>vault.readNote(p)));
      if(texts.every(t=>t===null||t.includes('source_id: '+job_id+'\n'))){rendered=candidate;break;}
    }
    if(!rendered)throw Error('Meeting title collision limit');
    for(const p of [rendered.note_path,rendered.transcript_path,rendered.log_path,rendered.audio_path,rendered.index_path].filter(Boolean)){
      checkedPath(p);if(!/^(00-Inbox\/会议\/|00-Test\/会议\/|90-Logs\/会议入库\/)/.test(p))throw Error('Meeting destination denied');
    }
    const prior=await vault.manipulator.get(rendered.audio_path,true);
    if(prior&&!prior.deleted){
      const saved=await readAttachment(vault,rendered.audio_path,128*1024*1024);
      if(saved.sha256!==job_id)throw Error('Existing audio conflict; preserved');
    }else{
      if(!await vault.manipulator.put(rendered.audio_path,await openAsBlob(audio),{ctime:Date.now(),mtime:Date.now(),size:bytes.length}))throw Error('Audio write failed');
      const saved=await readAttachment(vault,rendered.audio_path,128*1024*1024);
      if(saved.sha256!==job_id)throw Error('Audio readback failed');
    }
    const outputs=[[rendered.note_path,rendered.body],[rendered.transcript_path,rendered.transcript_body],[rendered.log_path,rendered.log]].filter(([p])=>p);
    for(const [p,text]of outputs){
      const old=await vault.readNote(p);
      if(old!==null&&!old.includes('source_id: '+job_id+'\n'))throw Error('Existing note conflict; preserved');
      if(old===null&&(!await vault.writeNote(p,text)||await vault.readNote(p)!==text))throw Error('Meeting note readback failed');
    }
    if(rendered.index_path){
      const old=await vault.readNote(rendered.index_path),marker=`<!-- meeting:${job_id} -->`;
      if(!old?.includes(marker)){
        const text=(old??'# 会议索引\n')+`\n- [[${rendered.note_path}]] ${marker}\n`;
        if(!await vault.writeNote(rendered.index_path,text)||await vault.readNote(rendered.index_path)!==text)throw Error('Meeting index readback failed');
      }
    }
    return {saved:true,job_id,note_path:rendered.note_path,transcript_path:rendered.transcript_path,
      audio_path:rendered.audio_path,log_path:rendered.log_path,source_preserved:true,
      audio_sha256:job_id,audio_bytes:bytes.length,semantic_curation:false};
  }
  return id=>{const next=tail.then(()=>run(id));tail=next.catch(()=>{});return next;};
}
