import {createHash} from 'node:crypto';

const digest = text => createHash('sha256').update(text, 'utf8').digest('hex');
const normalized = path => path.replace(/\.md$/i, '');
const safePath = path => typeof path === 'string' && !path.startsWith('/') && !path.includes('\\') && !path.split('/').some(p => !p || p === '..' || p === '.');
const linksIn = text => [...text.replace(/```[\s\S]*?```/g, '').matchAll(/\[\[([^\]]+)\]\]/g)].map(m => m[1].split('|')[0].split('#')[0].trim()).filter(Boolean);
const linksTo = (text, path) => linksIn(text).some(p => normalized(p) === normalized(path));
async function boundedMap(items, fn) {
  // Vault reads are independent, but cap concurrency for the small storage server.
  for(let at=0;at<items.length;at+=4)await Promise.all(items.slice(at,at+4).map(fn));
}

/** Deterministic validation. Never modifies notes, a ledger, or a checkpoint. */
export function createCurationVerifier({vault, queue, readReceipt, writeReceipt, readOperation, now = () => new Date().toISOString()}) {
  const locks = new Map();
  return async function verify(input) {
    const index = input.index;
    const inbox = index === null;
    if (!inbox && (input.source_id !== undefined || input.commit_key !== undefined || input.operation !== undefined)) throw Error('Mixed source identity');
    if (!inbox && (!Number.isInteger(index) || index < 0 || index >= queue.records.length)) throw Error('Invalid source index');
    let journal, canonical, receiptKey=index;
    if(inbox){
      if(typeof input.source_id!=='string'||!input.source_id.trim()||!['ingest','refresh','topic_repair','backfill'].includes(input.operation)||!(/^[a-f0-9]{64}$/.test(input.commit_key??''))||typeof readOperation!=='function')throw Error('Canonical Inbox operation identity required');
      journal=await readOperation(input.commit_key);
      canonical=resolveInboxOperation(journal,input);
      receiptKey='inbox-'+digest(JSON.stringify([input.source_id,input.operation,input.commit_key,canonical.prepared.snapshots.map(s=>[s.path,s.mtime,s.sha256]) ]));
    }
    const before = locks.get(receiptKey) ?? Promise.resolve();
    let release;
    const gate = new Promise(resolve => {release = resolve;});
    const held = before.then(() => gate);
    locks.set(receiptKey, held);
    await before;
    try {
      let args = input;
      if (input.verify_saved) {
        const saved = await readReceipt(receiptKey);
        if (!saved || !saved.request) throw Error('No saved program receipt for this source');
        args = {...saved.request, index, require_completion: true, verify_saved: false};
        if(inbox && (args.source_id!==input.source_id||args.commit_key!==input.commit_key||args.operation!==input.operation))throw Error('Saved receipt identity mismatch');
      }
      const record = inbox ? canonical.prepared.record : queue.records[index];
      if(inbox){
        const c=canonical;
        const expected={source_versions:c.prepared.snapshots.map(({path,mtime})=>({path,mtime})),knowledge_paths:c.plan.knowledge,source_record_path:c.plan.recordPath,log_path:c.plan.logPath,disposition:c.plan.disposition};
        for(const [key,value]of Object.entries(expected))if(args[key]!==undefined&&JSON.stringify(args[key])!==JSON.stringify(value))throw Error('Caller changed canonical operation '+key);
        args={...args,...expected};
      }
      const disposition = args.disposition ?? 'knowledge';
      if (!['knowledge', 'empty', 'no_knowledge'].includes(disposition)) throw Error('Invalid disposition');
      const emptyDisposition = disposition !== 'knowledge';
      const versions = args.source_versions ?? [];
      if (versions.length !== record.paths.length || new Set(versions.map(v => v.path)).size !== versions.length || record.paths.some(p => !versions.some(v => v.path === p))) throw Error('Versions must cover every source part exactly once');
      if (!emptyDisposition && (!(args.knowledge_paths?.length > 0) || args.knowledge_paths.length > 12)) throw Error('Provide one to twelve knowledge pages');
      if (emptyDisposition && args.knowledge_paths?.length) throw Error('Empty disposition must not include knowledge pages');
      const knowledge = [...new Set(args.knowledge_paths ?? [])];
      if (knowledge.some(p => !safePath(p) || !p.endsWith('.md') || !/^(20-Wiki|30-Projects)\//.test(p) || /\/(知识索引|总览)\.md$/.test(p))) throw Error('Invalid knowledge page scope');
      if (!safePath(args.source_record_path) || !args.source_record_path.startsWith('10-Sources/') || record.paths.includes(args.source_record_path)) throw Error('Use a separate source association record');
      if (!safePath(args.log_path) || !args.log_path.startsWith('90-Logs/')) throw Error('Invalid independent log path');
      const texts = new Map(), snapshots = {}, issues = [], unknown = new Set();
      const read = async path => {
        if (texts.has(path)) return texts.get(path);
        const text = await vault.readNote(path);
        if (typeof text !== 'string') throw Error('Cannot read note: ' + path);
        texts.set(path, text); snapshots[path] = digest(text);
        return text;
      };
      const sourceHashes = {};
      await boundedMap(versions, async version => {
        const [meta,text] = await Promise.all([vault.getMetadata(version.path),read(version.path)]);
        if (!meta || !Number.isFinite(Date.parse(version.mtime)) || new Date(meta.mtime).getTime() !== Date.parse(version.mtime)) issues.push('SOURCE_VERSION_CHANGED: ' + version.path);
        sourceHashes[version.path] = digest(text);
        if(inbox && sourceHashes[version.path]!==canonical.prepared.snapshots.find(s=>s.path===version.path).sha256)issues.push('SOURCE_HASH_CHANGED: '+version.path);
        if (disposition === 'empty' && text.trim()) issues.push('EMPTY_DISPOSITION_HAS_CONTENT: ' + version.path);
      });
      const [indexText,sourceRecord,log] = await Promise.all([read('20-Wiki/知识索引.md'),read(args.source_record_path),read(args.log_path)]);
      if (!sourceRecord.includes(record.source_id)) issues.push('SOURCE_ID_MISSING_FROM_RECORD');
      if (!log.includes(record.source_id)) issues.push('SOURCE_ID_MISSING_FROM_LOG');
      if (emptyDisposition && !new RegExp('^disposition:\\s*' + disposition + '\\s*$', 'm').test(sourceRecord)) issues.push('FILTER_DISPOSITION_MISSING_FROM_RECORD');
      for (const source of record.paths) if (!linksTo(sourceRecord, source)) issues.push('SOURCE_PART_BACKLINK_MISSING: ' + source);
      await boundedMap(knowledge,async path => {
        const text = await read(path);
        const body = text.replace(/^---\s*\n[\s\S]*?\n---\s*\n/, '').replace(/^#+[^\n]*$/gm, '').trim();
        if (!body) issues.push('EMPTY_KNOWLEDGE_PAGE: ' + path);
        for (const field of ['title', 'domain', 'type', 'status']) if (!new RegExp('^' + field + ':\\s*\\S', 'm').test(text)) issues.push('MISSING_METADATA_' + field + ': ' + path);
        if (!linksTo(indexText, path)) issues.push('NOT_INDEXED: ' + path);
        if (!linksTo(sourceRecord, path)) issues.push('SOURCE_TO_KNOWLEDGE_LINK_MISSING: ' + path);
        if (!record.paths.some(p => linksTo(text, p)) && !linksTo(text, args.source_record_path)) issues.push('KNOWLEDGE_TO_SOURCE_LINK_MISSING: ' + path);
      });
      const changed = [...knowledge, args.source_record_path, args.log_path];
      const targets = new Set(changed.flatMap(p => {
        const previous=inbox&&knowledge.includes(p)?canonical.plan.planned.find(w=>w.path===p)?.before:null;
        const oldLinks=linksIn(previous??'');
        const all=linksIn(texts.get(p));
        if(previous&&all.some(t=>oldLinks.includes(t)))unknown.add('Unchanged historical links retained; only newly introduced links checked for Inbox edits');
        return previous?all.filter(t=>!oldLinks.includes(t)):all;
      }));
      // Existing entity pages accumulate links across sources. Check every link
      // in bounded batches instead of failing a commit after its notes landed.
      const targetList = [...targets];
      for (let at = 0; at < targetList.length; at += 150) {
      await boundedMap(targetList.slice(at,at+150),async target => {
        if (!target.includes('/')) {unknown.add('Short-name links need global alias resolution'); return;}
        if (/\.[a-z0-9]+$/i.test(target) && !target.endsWith('.md')) {unknown.add('Binary attachments not checked'); return;}
        const path = target.endsWith('.md') ? target : target + '.md';
        if (!safePath(path)) {issues.push('UNSAFE_LINK: ' + target); return;}
        const value = texts.has(path)?texts.get(path):await vault.readNote(path);
        if (typeof value !== 'string') issues.push('BROKEN_FULL_PATH_LINK: ' + path);
      });
      }
      if (args.require_completion) {
        const ledger = await read('90-Logs/入库台账.md');
        const ledgerLines = ledger.split('\n').filter(line => line.includes(record.source_id));
        if (emptyDisposition) {
          if (!ledgerLines.some(line => new RegExp('status:\\s*' + disposition + '(?:\\s|[|｜]|$)').test(line) && versions.every(v => line.includes(v.mtime)))) issues.push('FILTER_DISPOSITION_LEDGER_MISSING');
          if (!/^processed:\s*false\s*$/m.test(sourceRecord)) issues.push('EMPTY_DISPOSITION_MUST_NOT_BE_PROCESSED');
        } else {
          if (!ledgerLines.length || !knowledge.every(p => ledgerLines.some(line => line.includes(normalized(p))))) issues.push('LEDGER_SOURCE_OR_KNOWLEDGE_MISSING');
          if (!/^processed:\s*true\s*$/m.test(sourceRecord)) issues.push('SOURCE_NOT_PROCESSED');
        }
        if(inbox){
          const final=await readOperation(input.commit_key);
          if(!final||final.status!=='PASS'||final.maintenance_finalized!==true)issues.push('OPERATION_NOT_FINALIZED');
          else if(digest(JSON.stringify(final.prepared))!==digest(JSON.stringify(canonical.prepared))||digest(JSON.stringify(final.plan))!==digest(JSON.stringify(canonical.plan)))issues.push('OPERATION_CHANGED');
          const marker=canonical.plan.marker;
          if(!sourceRecord.includes(marker)||!log.includes(marker)||!ledger.includes(marker))issues.push('OPERATION_MARKER_MISSING');
          for(const write of canonical.plan.planned){if(await read(write.path)!==write.after)issues.push('COMMITTED_OUTPUT_CHANGED: '+write.path);}
        } else {
        const checkpoint = await read('90-Logs/首次全量整理/当前批次.md');
        const next = checkpoint.match(/^next_index:\s*(\d+)\s*$/m);
        if (!next || Number(next[1]) < index + 1) issues.push('CHECKPOINT_NOT_ADVANCED');
        if (!checkpoint.includes(queue.catalog_sha256)) issues.push('CHECKPOINT_QUEUE_VERSION_MISMATCH');
        }
      }
      // Detect edits during validation. A prior write response alone is not validation.
      await boundedMap(Object.entries(snapshots),async ([path, expected]) => {
        const current = await vault.readNote(path);
        if (typeof current !== 'string' || digest(current) !== expected) issues.push('CHANGED_DURING_VALIDATION: ' + path);
      });
      const report = {
        version: inbox?2:1, checked_at: now(), queue_version: inbox?null:queue.catalog_sha256,
        index, source_id: record.source_id, status: issues.length ? 'FAIL' : 'PASS',
        disposition, counts_as_knowledge: !emptyDisposition,
        require_completion: Boolean(args.require_completion), issues, unknown: [...unknown],
        current_source_hashes: sourceHashes, verified_page_hashes: snapshots,
        checks: ['current source versions', 'complete source-part manifest', 'nonempty knowledge and required fields', 'index coverage', 'source associations', 'full-path note links', ...(args.require_completion ? ['ledger', 'processed state', ...(inbox?['finalized operation journal','exact committed outputs']:['checkpoint'])] : []), 'stable verification snapshot'],
        semantic_verification: 'Required from Chief; program does not verify factual support or contradictions.',
        global_lint: 'NOT RUN',
        request: {index, ...(inbox?{source_id:input.source_id,operation:input.operation,commit_key:input.commit_key}:{}), disposition, source_versions: versions, knowledge_paths: knowledge, source_record_path: args.source_record_path, log_path: args.log_path, require_completion: Boolean(args.require_completion)},
      };
      await writeReceipt(receiptKey, report);
      return {
        ...report, request: undefined, current_source_hashes: undefined, verified_page_hashes: undefined,
        source_parts_checked: versions.length, notes_checked: Object.keys(snapshots).length,
        receipt: 'server:curation-results/' + (inbox?receiptKey:String(index).padStart(6, '0')) + '.json',
        instruction: args.require_completion && !issues.length ? (emptyDisposition ? 'Empty-source disposition verified. Advance queue, retain raw source, count zero knowledge successes.' : 'Program completion checks passed. Semantic quality remains Chief responsibility.') : 'Never count completed until require_completion=true passes. Fix only reported issues; no repeated full-source reading without a changed version.',
      };
    } finally {
      release();
      if (locks.get(receiptKey) === held) locks.delete(receiptKey);
    }
  };
}


/** Only persisted program journals define Inbox bundles and outputs. Callers cannot invent paths. */
export function resolveInboxOperation(j,input){
  const p=j?.prepared,plan=j?.plan;
  if(!p||!plan||p.initial||p.key!==input.commit_key||p.record?.source_id!==input.source_id||p.operation!==input.operation||p.index!==null||p.full_coverage!==true)throw Error('Unknown or mismatched Inbox operation');
  if(!['APPLYING','FINALIZING','PASS'].includes(j.status))throw Error('Invalid operation journal state');
  if(!Array.isArray(p.record.paths)||!p.record.paths.length||new Set(p.record.paths).size!==p.record.paths.length||p.record.paths.some(path=>!safePath(path)||!path.endsWith('.md')))throw Error('Invalid canonical source bundle');
  if(p.snapshots?.length!==p.record.paths.length||p.record.paths.some(path=>p.snapshots.filter(s=>s.path===path).length!==1)||p.snapshots.some(s=>typeof s.text!=='string'||digest(s.text)!==s.sha256||!Number.isFinite(Date.parse(s.mtime))))throw Error('Invalid canonical source snapshots');
  const meeting=p.record.paths.filter(path=>/^00-Inbox\/会议\/[^/]+\/(会议纪要|完整转写)\.md$/.test(path));
  if(meeting.length){
    if(meeting.length!==2||p.record.paths.length!==2||new Set(meeting.map(path=>path.split('/').slice(0,-1).join('/'))).size!==1||new Set(meeting.map(path=>path.split('/').at(-1))).size!==2)throw Error('Incomplete meeting bundle');
    const audio=p.snapshots.map(s=>s.text.match(/^audio_sha256:\s*["']?([a-f0-9]{64})/m)?.[1]);
    if(audio.some(a=>!a)||new Set(audio).size!==1)throw Error('Meeting audio identity mismatch');
  }
  if(!Array.isArray(plan.knowledge)||!Array.isArray(plan.planned)||plan.marker!=='<!-- curation:'+p.key+' -->')throw Error('Invalid canonical operation plan');
  return {prepared:p,plan};
}
