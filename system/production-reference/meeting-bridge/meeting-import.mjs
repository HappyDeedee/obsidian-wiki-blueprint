import { createHash } from 'node:crypto';
import { createReadStream, openAsBlob } from 'node:fs';
import { readFile, stat } from 'node:fs/promises';
import { join } from 'node:path';
import { spawn } from 'node:child_process';
import { validAudioPath } from './inbox-audio-bridge.mjs';

const ID = /^[a-f0-9]{64}$/;
export const MAX_AUDIO_BYTES = 128 * 1024 * 1024;
let serial = Promise.resolve();
export const sha256 = data => createHash('sha256').update(data).digest('hex');

function text(value) {
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.map(text).filter(Boolean).join('\n\n');
  if (value && typeof value === 'object') {
    for (const key of ['Text', 'Summary', 'ParagraphSummary', 'Content', 'Title']) {
      if (value[key]) return text(value[key]);
    }
  }
  return '';
}
export function meetingTitle(result) {
  const candidate = text(result.minutes?.title || result.summary).split(/[。！？\n]/)[0].trim();
  return (candidate || '会议记录').replace(/[\\/:*?"<>|\[\]#\r\n]/g, ' ').replace(/\s+/g, ' ').slice(0, 40).trim() || '会议记录';
}
function recordingNameParts(name) {
  // Current phones use yy-MM-dd HH：mm with a Windows-safe fullwidth colon; retain earlier formats.
  const compact = /^(\d{2})-(\d{2})-(\d{2}) (\d{2})：(\d{2})(?: (.+))?\.[^.]+$/.exec(name)
    || /^(\d{2})-(\d{2})-(\d{2}) (\d{2})点(\d{2})分(?: (.+))?\.[^.]+$/.exec(name);
  if (compact) {
    const [,yy,month,day,hour,minute,location=''] = compact;
    return {year:`20${yy}`,month,day,hour,minute,location};
  }
  const legacy = /^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-/.exec(name);
  if (!legacy) return null;
  const [,year,day,month,hour,minute] = legacy;
  const location = name.match(/^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-(.+)\.[^.]+$/)?.[1] || '';
  return {year,month,day,hour,minute,location};
}
export function meetingDate(manifest) {
  const name = (manifest.source_path || '').split('/').at(-1) || '';
  const parts = recordingNameParts(name);
  if (parts) {
    const {year,day,month,hour,minute} = parts;
    const value = `${year}-${month}-${day}T${hour}:${minute}:00+08:00`;
    const date = new Date(value);
    if (Number.isFinite(date.getTime()) && new Date(date.getTime()+8*3600000).toISOString().slice(0,16) === value.slice(0,16)) {
      return {day:`${year}-${month}-${day}`, short:`${year.slice(2)}-${month}-${day}`, basis:'录音文件名日期（会议实际日期待核实）'};
    }
  }
  const date = new Date(manifest.created);
  if (!Number.isFinite(date.getTime())) throw Error('invalid_meeting_date');
  const day = new Date(date.getTime()+8*3600000).toISOString().slice(0,10);
  return {day, short:day.slice(2), basis:'服务器收件日期（未取得录音日期）'};
}
function meetingLayout(manifest, result) {
  const date = meetingDate(manifest);
  const base = `${date.short} ${meetingTitle(result)}`;
  const prefix = manifest.is_test === true ? '00-Test/会议' : '00-Inbox/会议';
  let title = manifest.layout_suffix ? `${base}（${manifest.layout_suffix}）` : base;
  if (manifest.note_path?.startsWith(prefix+'/')) {
    const parts=manifest.note_path.slice(prefix.length+1).split('/');
    if(parts.length===2){
      if(parts[1]!=='会议纪要.md' || !/^\d{2}-\d{2}-\d{2} /.test(parts[0])) throw Error('invalid_meeting_note_path');
      title=parts[0];
    }
  }
  const folder = `${prefix}/${title}`;
  return {date,title,folder,note_path:`${folder}/会议纪要.md`,
    transcript_path:`${folder}/完整转写.md`,audio_path:`${folder}/录音.${manifest.extension}`};
}
function timestamp(ms) {
  if (!Number.isFinite(ms) || ms < 0) return '';
  const seconds = Math.floor(ms / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}
export function renderLegacyMeeting(manifest, result) {
  const title = meetingTitle(result);
  const day = manifest.created.slice(0, 10);
  const time = manifest.created.slice(11, 19).replaceAll(':', '-');
  const stem = `${day}_${time}-${title}--${manifest.job_id.slice(0, 16)}`;
  const note_path = `${manifest.is_test === true ? '00-Test/会议' : '00-Inbox/会议'}/${stem}.md`;
  const audio_path = manifest.source_path || `00-Inbox/会议/录音/${manifest.job_id}.${manifest.extension}`;
  const log_path = `${manifest.is_test === true ? '00-Test/会议/处理记录' : '90-Logs/会议入库'}/${manifest.job_id}.md`;
  const paragraphs = result.paragraphs.map(p => {
    const position = timestamp(p.start_ms);
    const speaker = p.speaker_id == null ? '' : `发言人 ${p.speaker_id}`;
    return `${position || speaker ? `**${[position, speaker].filter(Boolean).join(' · ')}**\n\n` : ''}${p.text}`;
  }).join('\n\n');
  const actions = result.actions.map(action => {
    const content = text(action);
    const position = timestamp(action.Start);
    return content ? `- [ ] ${content.replace(/\n/g, ' ')}${position ? `（录音 ${position}）` : ''}` : '';
  }).filter(Boolean).join('\n');
  const body = `---\nkind: ${manifest.is_test === true ? 'test' : 'inbox'}\nprocessed: false\ntitle: ${JSON.stringify(title)}\ncreated: ${JSON.stringify(manifest.created)}\nsource_id: ${manifest.job_id}\naudio_sha256: ${manifest.job_id}\ntingwu_task_id: ${JSON.stringify(result.task_id)}\nsummary_provider: 阿里云听悟\ntitle_method: 摘要首句\n---\n# ${title}\n\n${manifest.is_test === true ? '这是合成语音测试，内容中的手机操作描述不代表 iPhone 已通过测试。\n\n' : ''}服务器接收时间：${manifest.created}（Asia/Shanghai）。标题由摘要首句生成，发言人编号来自自动识别。\n\n## 原始录音\n\n![[${audio_path}]]\n\n## 听悟自动摘要\n\n${text(result.summary) || '听悟未返回摘要内容。'}\n\n## 听悟识别的待办\n\n${actions || '听悟未识别到待办。'}\n\n## 完整转写原文\n\n${paragraphs || result.transcript || '听悟未返回可读转写。'}\n\n## 来源与处理记录\n\n录音 SHA256：${manifest.job_id}\n\n[[${log_path}]]\n`;
  const log = `---\nkind: source-record\nprocessed: false\nsource_id: ${manifest.job_id}\ncreated: ${JSON.stringify(manifest.created)}\n---\n# 会议原文收件记录\n\n来源：${manifest.source_path ? '知识库待处理目录同步录音' : '用户上传录音'}，听悟任务 ${result.task_id}。\n\n纪要：[[${note_path}]]\n\n原录音：[[${audio_path}]]\n\n录音 SHA256：${manifest.job_id}\n\n这是原文收件和听悟自动结果，不表示已完成知识提炼或人工核对。原始听悟 JSON 与录音处理副本保留在接收服务中。\n`;
  return { note_path, audio_path, log_path, body, log };
}

const GENERATED_START = '<!-- meeting-generated:v2:start -->';
const GENERATED_END = '<!-- meeting-generated:v2:end -->';
const list = value => Array.isArray(value) ? value : [];
const cell = value => (text(value) || '未明确').replaceAll('|', '｜').replace(/\r?\n/g, ' ');
const bullets = (items, empty) => list(items).map(item => `- ${text(item)}`).join('\n') || empty;
const mindLabel = value => String(value || '未命名主题').replace(/[\r\n\t]+/g,' ')
  .replaceAll('"','＂').replaceAll('\\','／').replaceAll('<','＜').replaceAll('>','＞')
  .replaceAll('`','｀').replaceAll('&','＆').replaceAll('[','［').replaceAll(']','］').trim();

export function renderMindMap(nodes) {
  let index = 0;
  const lines = ['mindmap'];
  function walk(node, depth) {
    if (depth > 8 || ++index > 1000) throw Error('meeting_mindmap_too_large');
    // Labels are data: remove Mermaid/HTML delimiters from model-generated text.
    const label = mindLabel(node.Title);
    lines.push(`${'  '.repeat(depth)}n${index}["${label}"]`);
    for (const child of list(node.Topic)) walk(child, depth + 1);
  }
  const roots = list(nodes);
  if (!roots.length) return '听悟未返回思维导图。';
  if (roots.length === 1) walk(roots[0], 1);
  else walk({Title:'会议主题', Topic:roots}, 1);
  const outline = [];
  function outlineWalk(node, depth) {
    outline.push(`${'  '.repeat(depth)}- ${mindLabel(node.Title)}`);
    for (const child of list(node.Topic)) outlineWalk(child, depth + 1);
  }
  roots.forEach(root => outlineWalk(root, 0));
  return '### 主题层级（手机阅读）\n\n' + outline.join('\n') +
    '\n\n> [!info]- 展开完整思维导图\n> \n> ```mermaid\n' +
    lines.map(line => '> '+line).join('\n') + '\n> ```';
}

export function renderQuestionAnswers(items, transcriptPath, positions) {
  const pairs = list(items);
  if (!pairs.length) return '听悟未识别到可整理的问答。';
  const quote = value => text(value).replace(/\r?\n/g, '\n> ');
  const evidence = ids => [...new Set(list(ids))].filter(id => Number.isInteger(id) && positions.has(id))
    .map(id => `[[${transcriptPath}#^sentence-${id}|句 ${id}${timestamp(positions.get(id)) ? '（'+timestamp(positions.get(id))+'）' : ''}]]`)
    .join('、') || '未提供可核对的句子编号。';
  return `> [!question]- 展开讨论问答（${pairs.length}组）\n> \n` + pairs.map((item, index) =>
    `> **${index+1}. 问：** ${quote(item.question)}\n> \n> **答：** ${quote(item.answer) || '原文未明确回答。'}\n> \n` +
    `> 提问原文：${evidence(item.question_sentence_ids)}\n> \n> 回答原文：${evidence(item.answer_sentence_ids)}`
  ).join('\n> \n> ---\n> \n');
}

export function renderMeeting(manifest, result) {
  if ((result.analysis_version || 1) < 2) return renderLegacyMeeting(manifest, result);
  const rendered = renderLegacyMeeting(manifest, result);
  const layout = meetingLayout(manifest,result);
  manifest={...manifest,wiki_source_id:manifest.wiki_source_id || result.wiki_source_id};
  if(manifest.wiki_source_id && !/^(?:meeting:[a-f0-9]{64}|incremental:[a-f0-9]{24})$/.test(manifest.wiki_source_id)) throw Error('invalid_meeting_source_id');
  if (manifest.layout_suffix && !/^\d{1,3}$/.test(String(manifest.layout_suffix))) throw Error('invalid_meeting_note_path');
  // Existing v2 paths are retained only as provenance; current output follows one layout.
  if (manifest.note_path && /[\\\r\n\x00\[\]|#^]|(?:^|\/)\.\.(?:\/|$)/.test(manifest.note_path)) throw Error('invalid_meeting_note_path');
  const oldNotePath = rendered.note_path, oldAudioPath = rendered.audio_path;
  rendered.note_path = layout.note_path;
  rendered.audio_path = layout.audio_path;
  rendered.log = rendered.log.replace(`[[${oldNotePath}]]`,`[[${rendered.note_path}]]`)
    .replace(`[[${oldAudioPath}]]`,`[[${rendered.audio_path}]]`);
  const minutes = result.minutes || {};
  rendered.transcript_path = layout.transcript_path;
  rendered.index_path = `${manifest.is_test === true ? '00-Test/会议' : '00-Inbox/会议'}/会议索引.md`;
  const numbering = ['一','二','三','四','五','六','七','八','九','十'];
  const topics = list(minutes.topics).map((topic, i) =>
    `### （${numbering[i] || i+1}）${cell(topic.title)}\n\n${text(topic.discussion)}${text(topic.conclusion) && !/^未明确[。.]?$/.test(text(topic.conclusion)) ? `\n\n${text(topic.conclusion)}` : ''}`
  ).join('\n\n') || '未生成分议题纪要。';
  const positions = new Map();
  for (const p of result.raw_results?.Transcription?.Transcription?.Paragraphs || []) {
    for (const w of p.Words || []) if (!positions.has(w.SentenceId)) positions.set(w.SentenceId, w.Start);
  }
  const actionItems = list(minutes.action_items);
  const actions = actionItems.length ?
    '| 事项 | 负责人 | 截止时间 | 原文依据 |\n| --- | --- | --- | --- |\n' +
    actionItems.map(a => `| ${cell(a.task)} | ${cell(a.owner)} | ${a.deadline ? cell(a.deadline) : '待确认（原文未明确）'} | ${list(a.evidence_sentence_ids).map(id => positions.has(id) ? `[[${rendered.transcript_path}#^sentence-${id}\\|句 ${id}（${timestamp(positions.get(id))}）]]` : `句 ${id}`).join('、')} |`).join('\n') +
    '\n\n日期按原文保留；“下周”等相对时间不按收件日期换算。未说明期限的事项，需要会后确认。' :
    '自定义纪要未提取到有明确证据的行动项。';
  const speakers = list(result.speaker_summaries).map(s =>
    `### ${cell(s.SpeakerName || `发言人 ${s.SpeakerId ?? '未知'}`)}\n\n${text(s.Summary)}`
  ).join('\n\n') || '听悟未返回发言总结。';
  const chapters = list(result.chapters).map(c =>
    `- **${timestamp(c.Start)}–${timestamp(c.End)} ${cell(c.Headline)}**\n  ${text(c.Summary).replace(/\n/g, '\n  ')}`
  ).join('\n\n') || '听悟未返回章节速览。';
  const warnings = list(result.analysis_warnings).map(w => `> [!warning] 纪要完整性提示\n> ${w}`).join('\n\n');
  const received = new Date(new Date(manifest.created).getTime()+8*3600000).toISOString().slice(0,16).replace('T',' ');
  const originalName=(manifest.source_path || '').split('/').at(-1) || '';
  const location=recordingNameParts(originalName)?.location || '';
  const originalLabel=originalName.replaceAll('`','ˋ');
  const questionAnswers = (result.analysis_version >= 3 || list(result.question_answers).length) ?
    `## 问答回顾\n\n${renderQuestionAnswers(result.question_answers, rendered.transcript_path, positions)}\n\n` : '';
  const generated = `## 原始录音\n\n![[${rendered.audio_path}]]\n\n[[${rendered.transcript_path}|查看完整转写原文]]\n\n## 背景概述\n\n${warnings ? warnings+'\n\n' : ''}${text(minutes.overview || result.summary)}\n\n## 一、重点工作进展与讨论\n\n${topics}\n\n## 二、问题与待确认事项\n\n${bullets(minutes.open_questions, '未提取到待确认问题。')}\n\n## 三、共识与结论\n\n${bullets(minutes.decisions, '原文未提取到明确达成的决策。')}\n\n## 四、下一步工作安排\n\n${actions}\n\n## 发言总结\n\n发言人编号由听悟自动识别，身份未经人工确认。\n\n${speakers}\n\n## 思维导图\n\n${renderMindMap(result.mind_map)}\n\n## 章节速览\n\n${chapters}\n\n${questionAnswers}## 来源与处理记录\n\n自动整理结果，请结合原录音核对。\n\n标题日期：${layout.date.day}；依据：${layout.date.basis}。\n\n原录音文件名：${originalLabel ? "`"+originalLabel+"`" : "未提供"}。\n\n服务器接收时间：${received}（北京时间）。\n\n[[${rendered.log_path}]]`;
  rendered.body = `---\nkind: ${manifest.is_test === true ? 'test' : 'source'}\nprocessed: false\ntitle: ${JSON.stringify(layout.title)}\ndate: ${JSON.stringify(layout.date.day)}\ndate_basis: ${JSON.stringify(layout.date.basis)}\noriginal_filename: ${JSON.stringify(originalName)}\nfilename_location: ${JSON.stringify(location)}\noriginal_source_path: ${JSON.stringify(manifest.source_path || "")}\ncreated: ${JSON.stringify(manifest.created)}\nsource_id: ${manifest.job_id}\naudio_sha256: ${manifest.job_id}\ntingwu_task_id: ${JSON.stringify(result.task_id)}\nsummary_provider: 阿里云听悟\nanalysis_version: ${result.analysis_version || 2}\nlayout_version: 4\nwiki_source_id: ${manifest.wiki_source_id || "meeting:"+manifest.job_id}\ntitle_method: 录音日期与自定义纪要主题\n---\n# ${layout.title}\n\n${manifest.is_test === true ? '这是合成语音测试，不代表 iPhone 已通过测试。\n\n' : ''}${generated}\n`;
  const rawParagraphs = list(result.raw_results?.Transcription?.Transcription?.Paragraphs);
  const seenSentences = new Set();
  const source = rawParagraphs.length ? rawParagraphs.map(p => {
    const groups = [];
    for (const word of list(p.Words)) {
      const last = groups[groups.length-1];
      if (last && last.id === word.SentenceId) last.content += word.Text || '';
      else groups.push({id:word.SentenceId,start:word.Start,content:word.Text || ''});
    }
    return groups.map(g => {
      const anchor = Number.isInteger(g.id) && !seenSentences.has(g.id) ? `\n\n^sentence-${g.id}` : '';
      seenSentences.add(g.id);
      return `**${timestamp(g.start)} · 发言人 ${p.SpeakerId ?? '未知'}**\n\n${g.content}${anchor}`;
    }).join('\n\n');
  }).join('\n\n') : result.paragraphs.map(p =>
    `**${timestamp(p.start_ms)} · 发言人 ${p.speaker_id ?? '未知'}**\n\n${p.text}`
  ).join('\n\n') || result.transcript || '听悟未返回可读转写。';
  rendered.transcript_body = `---\nkind: source-transcript\nprocessed: false\ntitle: "完整转写"\nmeeting_title: ${JSON.stringify(layout.title)}\naudio_sha256: ${manifest.job_id}\nwiki_source_id: ${manifest.wiki_source_id || "meeting:"+manifest.job_id}\nsource_id: ${manifest.job_id}\ntingwu_task_id: ${JSON.stringify(result.task_id)}\n---\n# 完整转写\n\n会议：${layout.title}\n\n[[${rendered.note_path}|返回会议纪要]]\n\n## 原始录音\n\n![[${rendered.audio_path}]]\n\n## 完整转写原文\n\n发言人编号为自动识别结果。\n\n${source}\n\n[[${rendered.note_path}|返回会议纪要]]\n`;
  return rendered;
}

export function mergeMeetingNote(existing, incoming, previousGenerated) {
  if (existing === null || existing === incoming) return incoming;
  // Only replace an exact known previous rendering. Never discard user edits.
  if (typeof previousGenerated === 'string' && sha256(existing) === previousGenerated) return incoming;
  // Preserve edited text verbatim, adding a clearly delimited new analysis once.
  if (existing.includes(GENERATED_START)) throw Error('meeting_note_conflict_preserved');
  const start = incoming.indexOf(GENERATED_START);
  if (start < 0) throw Error('meeting_note_conflict_preserved');
  return existing.trimEnd() + '\n\n---\n\n' + incoming.slice(start);
}
async function boundedJSON(path) {
  if ((await stat(path)).size > 32 * 1024 * 1024) throw Error('meeting_json_too_large');
  return JSON.parse(await readFile(path, 'utf8'));
}
async function fileHash(path) {
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(path)) hash.update(chunk);
  return hash.digest('hex');
}
async function verifiedAudio(vault,path,hash,size) {
  const {decodeBinary} = await import('octagonal-wheels/binary');
  try {
    const doc = await vault.manipulator.get(path);
    if (!doc || doc.deleted) return null;
    if (doc.type !== 'newnote' || !Array.isArray(doc.data)) throw Error('source_audio_changed_preserved');
    const bytes = Buffer.from(decodeBinary(doc.data));
    if (bytes.length !== size || sha256(bytes) !== hash) throw Error('source_audio_changed_preserved');
    return {revision:doc._rev};
  } finally {vault.manipulator.liveSyncLocalDB.clearCaches();}
}
async function isolatedAudio(op,path,hash,size) {
  return new Promise((resolve,reject)=>{
    // Large binary chunks are never retained in the long-lived MCP/search process.
    const child=spawn(process.execPath,['--expose-gc','--max-old-space-size=384','/app/meeting/inbox-audio-bridge.mjs'],{
      stdio:['pipe','pipe','ignore'],env:{...process.env,MEETING_AUDIO_MAX_BYTES:String(MAX_AUDIO_BYTES)}});
    let output='',ready=false,result;
    const timer=setTimeout(()=>{child.kill();reject(Error('meeting_audio_worker_timeout'));},150000);
    child.stdout.setEncoding('utf8');
    child.stdout.on('data',chunk=>{
      output+=chunk;
      if(output.length>256000){child.kill();return reject(Error('meeting_audio_worker_failed'));}
      let newline;
      while((newline=output.indexOf('\n'))>=0){
        const line=output.slice(0,newline);output=output.slice(newline+1);
        try {
          const message=JSON.parse(line);
          if(!ready){if(!message.ready)throw Error();ready=true;child.stdin.end(JSON.stringify({op,path,sha256:hash,size})+'\n');}
          else result=message;
        }catch{child.kill();reject(Error('meeting_audio_worker_failed'));}
      }
    });
    child.on('error',()=>{clearTimeout(timer);reject(Error('meeting_audio_worker_failed'));});
    child.on('close',code=>{
      clearTimeout(timer);
      if(code!==0 || !result?.ok)return reject(Error(result?.error==='audio_changed_preserved'?'source_audio_changed_preserved':'meeting_audio_worker_failed'));
      resolve(result.entry);
    });
  });
}

export async function importMeeting(vault, searchIndex, job_id, writable, directory = '/app/meeting-data') {
  // Serialize large audio operations, including calls from separate MCP sessions.
  const operation = serial.then(async () => {
    if (!ID.test(job_id)) throw Error('invalid_meeting_id');
    const manifest = await boundedJSON(join(directory, `${job_id}.export.json`));
    const result = await boundedJSON(join(directory, `${job_id}.result.json`));
    if (manifest.job_id !== job_id || result.job_id !== job_id || manifest.task_id !== result.task_id ||
        !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/.test(manifest.created) ||
        !['m4a','wav','mp3','aac','caf','mp4'].includes(manifest.extension) ||
        !Array.isArray(result.paragraphs) || !Array.isArray(result.actions)) throw Error('invalid_meeting_manifest');
    if (manifest.source_path && (!validAudioPath(manifest.source_path) ||
        !manifest.source_path.toLowerCase().endsWith('.'+manifest.extension))) throw Error('invalid_meeting_source_path');
    let rendered = renderMeeting(manifest, result);
    const verifiedRevisions=new Map();
    const verifyAudio = async(path,hash,size) => {
      if(directory!=='/app/meeting-data')return verifiedAudio(vault,path,hash,size);
      const meta=await vault.manipulator.get(path,true);
      if(!meta || meta.deleted)return null;
      if(verifiedRevisions.get(path)?.revision===meta._rev)return verifiedRevisions.get(path);
      const checked=await isolatedAudio('verify_archive',path,hash,size);
      if(checked)verifiedRevisions.set(path,checked);
      return checked;
    };
    const archived = (result.analysis_version || 1) >= 2;
    if (archived) {
      // One folder per meeting; same date/title must never overwrite another recording.
      for (let suffix=1; suffix<=999; suffix++) {
        const candidate=renderMeeting({...manifest,layout_suffix:suffix===1 ? '' : String(suffix)},result);
        const note=await vault.readNote(candidate.note_path);
        const transcript=await vault.readNote(candidate.transcript_path);
        const same = value => value === null || value.includes(`source_id: ${job_id}\n`);
        let audioConflict=false;
        if (same(note) && same(transcript)) {
          try {await verifyAudio(candidate.audio_path,job_id,manifest.bytes);}
          catch (error) {if (error.message!=='source_audio_changed_preserved') throw error; audioConflict=true;}
          if (!audioConflict) {rendered=candidate;break;}
        }
        if (manifest.note_path?.startsWith((manifest.is_test ? '00-Test/会议/' : '00-Inbox/会议/'))) throw Error('meeting_note_conflict_preserved');
        if (suffix===999) throw Error('meeting_title_collision_limit');
      }
    }
    for (const path of [rendered.note_path, rendered.audio_path, rendered.log_path, rendered.transcript_path,rendered.index_path,manifest.source_path].filter(Boolean)) {
      if (!writable(path)) throw Error('meeting_write_scope_denied');
    }
    const audio = join(directory, `${job_id}.${manifest.extension}`);
    const size = (await stat(audio)).size;
    if (size !== manifest.bytes || size < 1) throw Error('meeting_audio_size_mismatch');
    // Bound peak memory of LiveSync's binary chunk encryption and readback.
    if (size > MAX_AUDIO_BYTES) throw Error('meeting_audio_attachment_over_limit');
    if (await fileHash(audio) !== job_id) throw Error('meeting_audio_hash_mismatch');
    let attachment;
    let intake;
    if (archived) {
      // Keep intake untouched until the archived binary and both notes are verified.
      if (manifest.source_path) intake=await verifyAudio(manifest.source_path,job_id,size);
      const existing=await verifyAudio(rendered.audio_path,job_id,size);
      if (!existing) {
        if (manifest.source_path && !intake) throw Error('source_audio_not_ready');
        if (directory==='/app/meeting-data') {
          if (!await isolatedAudio('save_archive',rendered.audio_path,job_id,size)) throw Error('meeting_audio_readback_failed');
        } else {
          if (!await vault.manipulator.put(rendered.audio_path,await openAsBlob(audio),{ctime:Date.now(),mtime:Date.now(),size})) throw Error('meeting_audio_write_failed');
          if (!await verifyAudio(rendered.audio_path,job_id,size)) throw Error('meeting_audio_readback_failed');
        }
      }
      attachment={sha256:job_id,duplicate:!!existing};
    } else if (manifest.source_path) {
      // Reuse the original named attachment; no second binary attachment is written.
      const source = await vault.manipulator.get(manifest.source_path);
      if (!source || source.deleted || source.type !== 'newnote' || !Array.isArray(source.data)) throw Error('source_audio_not_ready');
      const {decodeBinary} = await import('octagonal-wheels/binary');
      const original = Buffer.from(decodeBinary(source.data));
      if (original.length !== size || sha256(original) !== job_id) throw Error('source_audio_changed_preserved');
      vault.manipulator.liveSyncLocalDB.clearCaches();
      attachment = {sha256:job_id,duplicate:true};
    } else attachment = await vault.saveMeetingAudio(rendered.audio_path, await openAsBlob(audio), job_id, size);
    if (rendered.transcript_path) {
      const previousTranscript = await vault.readNote(rendered.transcript_path);
      if (previousTranscript === null) {
        if (!await vault.writeNote(rendered.transcript_path, rendered.transcript_body)) throw Error('meeting_transcript_write_failed');
        if (await vault.readNote(rendered.transcript_path) !== rendered.transcript_body) throw Error('meeting_transcript_readback_failed');
        searchIndex.update(rendered.transcript_path, rendered.transcript_body, Date.now());
      } else if (!previousTranscript.includes(`source_id: ${job_id}\n`)) throw Error('meeting_transcript_conflict_preserved');
    }
    const existing = await vault.readNote(rendered.note_path);
    if (existing === null) {
      if (!await vault.writeNote(rendered.note_path, rendered.body)) throw Error('meeting_note_write_failed');
      if (await vault.readNote(rendered.note_path) !== rendered.body) throw Error('meeting_note_readback_failed');
      searchIndex.update(rendered.note_path, rendered.body, Date.now());
    } else if (!existing.includes(`source_id: ${job_id}\n`) || !existing.includes(`![[${rendered.audio_path}]]`)) {
      throw Error('meeting_note_conflict_preserved');
    } else if ((result.analysis_version || 1) >= 2 && manifest.upgrade_previous_sha256 &&
        sha256(existing) === manifest.upgrade_previous_sha256) {
      const upgraded = mergeMeetingNote(existing, rendered.body, manifest.upgrade_previous_sha256);
      if (!await vault.writeNote(rendered.note_path, upgraded)) throw Error('meeting_note_write_failed');
      if (await vault.readNote(rendered.note_path) !== upgraded) throw Error('meeting_note_readback_failed');
      searchIndex.update(rendered.note_path, upgraded, Date.now());
    }
    const existingLog = await vault.readNote(rendered.log_path);
    if (existingLog === null) {
      if (!await vault.writeNote(rendered.log_path, rendered.log)) throw Error('meeting_log_write_failed');
      if (await vault.readNote(rendered.log_path) !== rendered.log) throw Error('meeting_log_readback_failed');
      searchIndex.update(rendered.log_path, rendered.log, Date.now());
    } else if (!existingLog.includes(`source_id: ${job_id}\n`)) throw Error('meeting_log_conflict_preserved');
    if (archived) {
      const previousIndex=await vault.readNote(rendered.index_path);
      const marker=`<!-- meeting:${job_id} -->`;
      if (!previousIndex?.includes(marker)) {
        const heading=previousIndex || '---\nkind: guide\n---\n# 会议索引\n\n每条会议包含纪要、完整转写和原录音。自动结果待人工核对；知识提炼进度另见知识索引。\n\n';
        const updated=heading.trimEnd()+`\n\n- [[${rendered.note_path}|${rendered.note_path.split('/').at(-2)}]] ${marker}\n`;
        if (!await vault.writeNote(rendered.index_path,updated) || await vault.readNote(rendered.index_path)!==updated) throw Error('meeting_index_write_failed');
        searchIndex.update(rendered.index_path,updated,Date.now());
      }
    }
    if (archived && intake) {
      const before=await vault.manipulator.get(manifest.source_path,true);
      if (!before || before.deleted || before._rev!==intake.revision) throw Error('source_audio_changed_preserved');
      // Verify actual retained notes before removing the original intake entry.
      for (const path of [rendered.note_path,rendered.transcript_path]) {
        if (!(await vault.readNote(path))?.includes(`![[${rendered.audio_path}]]`)) throw Error('meeting_archive_link_unverified');
      }
      if (!await vault.manipulator.delete(manifest.source_path)) throw Error('meeting_intake_archive_pending');
      vault.manipulator.liveSyncLocalDB.clearCaches();
      const remaining=await vault.manipulator.get(manifest.source_path,true);
      if (remaining && !remaining.deleted) throw Error('meeting_intake_archive_pending');
    }
    return { saved: true, job_id, note_path: rendered.note_path, transcript_path: rendered.transcript_path, audio_path: rendered.audio_path,
      log_path: rendered.log_path, audio_sha256: attachment.sha256, audio_bytes: size, duplicate: existing !== null,
      ...(archived ? {layout_version:4,index_path:rendered.index_path,source_archived:!!manifest.source_path,source_path:manifest.source_path || ''} : {}) };
  });
  serial = operation.catch(() => {});
  return operation;
}
