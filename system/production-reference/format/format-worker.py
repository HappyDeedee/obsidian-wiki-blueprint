"""Convert received Inbox attachments using upstream's MarkItDown approach.
Runs serially from a systemd timer; only creates derived source text, never wiki pages.
"""
import base64, datetime, fcntl, hashlib, json, os, re, subprocess, tempfile, time, select
from pathlib import Path
ROOT=Path(__file__).resolve().parent
CONVERTER=Path('/opt/personal-kb-format')
SCRATCH=Path('/var/lib/personal-kb-format/tmp')
STATE=ROOT/'state.json'
SUPPORTED={'.pdf','.pptx','.docx','.xlsx','.xls','.html','.htm','.txt','.csv','.json','.xml','.epub','.ipynb'}
TEST_PREFIX=os.environ.get('KB_FORMAT_TEST_PREFIX')
BRIDGE=None
def bridge(value):
    global BRIDGE
    if BRIDGE is None:
        BRIDGE=subprocess.Popen(['sudo','docker','exec','-i',*(['-e','KB_FORMAT_TEST_PREFIX='+TEST_PREFIX] if TEST_PREFIX else []),'obsidian-mcp','node',('/app/curation/00-Test-v19-bh/attachment-bridge.mjs' if TEST_PREFIX else '/app/curation/attachment-bridge.mjs')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8')
    try:
        BRIDGE.stdin.write(json.dumps(value,ensure_ascii=False)+'\n');BRIDGE.stdin.flush()
        if not select.select([BRIDGE.stdout],[],[],90)[0]:raise RuntimeError('attachment_bridge_timeout')
        line=BRIDGE.stdout.readline()
        if not line:raise RuntimeError('attachment_bridge_closed')
        result=json.loads(line)
    except Exception:
        close_bridge();raise
    if result.get('error'):raise RuntimeError(result['error'])
    return result
def close_bridge():
    global BRIDGE
    if BRIDGE is not None:
        try:
            BRIDGE.stdin.close();BRIDGE.wait(timeout=10)
        except (BrokenPipeError,subprocess.TimeoutExpired):
            BRIDGE.kill();BRIDGE.wait()
        finally:BRIDGE=None
def atomic(path,value):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');tmp.chmod(0o600);tmp.replace(path)
def classify(path):
    ext=Path(path).suffix.lower()
    if ext in SUPPORTED:return 'convert'
    if ext in {'.png','.jpg','.jpeg','.webp','.gif','.bmp','.tif','.tiff'}:return 'requires_ocr_or_vision'
    if ext in {'.mp3','.wav','.m4a','.aac','.caf','.ogg','.flac'}:return 'registered_audio_no_transcription'
    if ext in {'.mp4','.mov','.mkv','.webm'}:return 'requires_video_processing'
    return 'unsupported_format'
def run():
    state=json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {'files':{}}
    entries=bridge({'action':'list'})['entries'];done=0
    receipts={};receipt_fingerprint=[]
    for receipt_path in Path('/srv/wiki/meeting/data').glob('*.obsidian.json'):
        try:
            stat=receipt_path.stat();receipt_fingerprint.append((receipt_path.name,stat.st_mtime_ns,stat.st_size))
            receipt=json.loads(receipt_path.read_text(encoding='utf-8'))
            if receipt.get('saved')and receipt.get('audio_path','').startswith('00-Inbox/')and receipt.get('note_path'):
                receipts[receipt['audio_path']]=receipt
        except (OSError,ValueError):continue
    test_prefix=os.environ.get('KB_FORMAT_TEST_PREFIX')
    if test_prefix:
        if not re.fullmatch(r'00-Test/v19-bh/[0-9a-f]{12}/',test_prefix):raise ValueError('Invalid test prefix')
        entries=[e for e in entries if e['path'].startswith(test_prefix)]
    else:
        fingerprint=json.dumps({
            'entries':sorted((e.get('path'),e.get('mtime'),e.get('size'),e.get('revision')) for e in entries),
            'receipts':sorted(receipt_fingerprint),
        },ensure_ascii=False,separators=(',',':'))
        terminal={'TEXT_EXTRACTED','TRANSCRIBED','TRANSCRIBED_TEST','requires_ocr_or_vision','registered_audio_no_transcription','requires_transcription','meeting_chain_only','requires_video_processing','unsupported_format','too_large','needs_review'}
        has_pending=any(state['files'].get(e['path'],{}).get('status') not in terminal for e in entries)
        if state.get('scan_fingerprint')==fingerprint and not has_pending:
            print(json.dumps({'scanned':len(entries),'attempted':0,'unchanged':True},ensure_ascii=False))
            return
    for entry in entries:
        path=entry['path'];old=state['files'].get(path,{})
        identity=[entry.get('mtime'),entry.get('size'),entry.get('revision')]
        receipt=receipts.get(path)
        if receipt and bridge({'action':'transcript_status','path':receipt['note_path']}).get('exists'):
            state['files'][path]={'identity':identity,'checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'TRANSCRIBED_TEST'if receipt['note_path'].startswith('00-Test/')else'TRANSCRIBED','output':receipt['note_path']}
            atomic(STATE,state);continue
        if old.get('identity')==identity and old.get('status') in {'TEXT_EXTRACTED','requires_ocr_or_vision','registered_audio_no_transcription','meeting_chain_only','requires_video_processing','unsupported_format','too_large','needs_review'}:continue
        if old.get('identity')==identity and old.get('retry_after',0)>time.time():continue
        row={'identity':identity,'checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
        kind=classify(path)
        if path.startswith('00-Inbox/会议/') and kind=='registered_audio_no_transcription':kind='meeting_chain_only'
        if kind!='convert':row['status']=kind
        elif entry.get('size',0)>25*1024*1024:row['status']='too_large'
        elif time.time()*1000-entry.get('mtime',0)<60000:continue
        elif done>=3:continue
        else:
            try:
                attachment=bridge({'action':'read','path':path})
                data=base64.b64decode(attachment['data'],validate=True)
                sha=hashlib.sha256(data).hexdigest()
                if sha!=attachment['sha256']:raise RuntimeError('attachment_checksum_mismatch')
                reused=next((v for v in state['files'].values() if v.get('source_sha256')==sha and v.get('status')=='TEXT_EXTRACTED' and v.get('output')),None)
                if reused and bridge({'action':'transcript_status','path':reused['output']}).get('exists'):
                    row.update(status='TEXT_EXTRACTED',source_sha256=sha,output=reused['output'],reused_content=True)
                    state['files'][path]=row;atomic(STATE,state);continue
                done+=1
                with tempfile.TemporaryDirectory(prefix='kb-convert-',dir=SCRATCH) as temp:
                    Path(temp).chmod(0o755)
                    source=Path(temp)/('source'+Path(path).suffix.lower());source.write_bytes(data)
                    source.chmod(0o644)
                    outdir=Path(temp)/'output';outdir.mkdir(mode=0o777);outdir.chmod(0o777)
                    output=outdir/'output.md'
                    r=subprocess.run(['sudo','systemd-run','--quiet','--wait','--pipe','--collect',
                        '--property=User=nobody','--property=Group=nogroup',
                        '--property=PrivateNetwork=yes','--property=PrivateTmp=yes',
                        '--property=ProtectSystem=strict','--property=ProtectHome=yes',
                        '--property=NoNewPrivileges=yes','--property=MemoryMax=320M',
                        '--property=MemorySwapMax=0','--property=CPUQuota=50%',
                        '--property=RuntimeMaxSec=110','--property=LimitFSIZE=4194304',
                        '--property=ReadWritePaths='+str(outdir),
                        str(CONVERTER/'.venv/bin/python'),str(CONVERTER/'format-convert.py'),str(source),str(output)],capture_output=True,text=True,encoding='utf-8',timeout=125)
                    if r.returncode:raise RuntimeError('conversion_failed: '+r.stderr[-350:])
                    text=output.read_text(encoding='utf-8')
                if len(text)>1_000_000:raise RuntimeError('extracted_text_exceeds_budget')
                stem=re.sub(r'[\\/:*?"<>|\[\]#\x00-\x1f]','_',Path(path).stem)[:90]
                target=(test_prefix+'已提取正文/' if test_prefix else '00-Inbox/已提取正文/')+stem+'-'+hashlib.sha256(path.encode()).hexdigest()[:6]+'-'+sha[:12]+'.md'
                kind='test' if test_prefix else 'source'
                body='---\n'+f'title: {json.dumps(Path(path).name,ensure_ascii=False)}\nkind: {kind}\nconversion_status: text_extracted\nsource_attachment: {json.dumps(path,ensure_ascii=False)}\nsource_sha256: {sha}\nconverter: Microsoft-MarkItDown\n'+'---\n\n# '+Path(path).name+'\n\n原始附件：[['+path+']]\n\n以下为程序提取的文字，未核验扫描图、图表、嵌入图片与音视频内容；文字提取完成不代表附件全部内容已读。\n\n## 提取正文\n\n'+text
                bridge({'action':'write','path':target,'content':body})
                row.update(status='TEXT_EXTRACTED',source_sha256=sha,output=target,characters=len(text))
            except Exception as e:
                attempts=(old.get('attempts',0) if old.get('identity')==identity else 0)+1
                row.update(status='needs_review' if attempts>=3 else 'retry_pending',attempts=attempts,retry_after=time.time()+1800,error=str(e)[:250])
        state['files'][path]=row;atomic(STATE,state)
    state['last_scan']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    if not test_prefix:state['scan_fingerprint']=fingerprint
    atomic(STATE,state)
    if not test_prefix:
        labels={'TRANSCRIBED':'已有转写正文，等待或进入知识整理','TRANSCRIBED_TEST':'已有测试转写正文，不计正式知识入库','TEXT_EXTRACTED':'已提取文字，等待或进入知识整理','requires_ocr_or_vision':'待识图或 OCR','registered_audio_no_transcription':'仅登记原件，未转写；需要转写请放会议/待处理','meeting_chain_only':'会议链路负责，通用转换不处理','requires_video_processing':'待字幕或视频处理','unsupported_format':'格式未接通','too_large':'超过 25 MB','needs_review':'多次失败，需要检查','retry_pending':'等待重试'}
        report='---\nkind: status\n---\n# 附件转换状态\n\n文字提取与知识整理是两个步骤。原附件保留；扫描图片、图表和音视频不计为已读。\n\n'
        for entry in sorted(entries,key=lambda e:e['path']):
            p=entry['path'];row=state['files'].get(p,{})
            report+='- [['+p+']]：'+labels.get(row.get('status'),'等待文件稳定或下一轮处理')+('；[['+row['output']+']]' if row.get('output') else '')+'\n'
        bridge({'action':'report','path':'90-Logs/附件转换/当前状态.md','content':report})
    print(json.dumps({'scanned':len(entries),'attempted':done,'states':{p:r['status'] for p,r in state['files'].items()}},ensure_ascii=False))
if __name__=='__main__':
    with (ROOT/'worker.lock').open('w') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit(0)
        try:run()
        finally:close_bridge()
