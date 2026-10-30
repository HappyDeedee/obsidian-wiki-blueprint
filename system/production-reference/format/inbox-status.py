"""Refresh the human-readable pending view from trusted server receipts."""
import importlib.util,json,pathlib,sys,os
ROOT=pathlib.Path(os.environ.get('WIKI_STACK_ROOT','/srv/wiki/stack'))
spec=importlib.util.spec_from_file_location('vc',ROOT/'vault-client.py');vc=importlib.util.module_from_spec(spec);spec.loader.exec_module(vc)
credentials=json.loads((ROOT/'credentials.json').read_text());c=vc.MCP(os.environ.get('WIKI_MCP_URL',credentials.get('mcp_url','https://wiki.example.invalid/mcp')),credentials['mcp_token'],timeout=45)
data=json.loads(c.tool('curation_pending'))
lines=['---','kind: status','---','# 未整理资料','','由当前版本和正式提交记录生成。已整理且内容未变的文件不在此列；旧原件中的 processed 不作成功依据。全量期间资料可以排队，BUSY时不抢占。','','## 待整理','']
lines += ['- '+'、'.join('[['+p+']]' for p in x['paths']) for x in data['pending']] or ['暂无。']
for status,title in [('WAITING_STABLE_VERSION','日记等待同一内容版本稳定6小时'),('HISTORICAL_STATE_UNVERIFIED','历史状态待核：有旧标记，缺可信回执')]:
 lines+=['','## '+title,''];lines+=['- [['+x['path']+']]' for x in data.get('deferred',[]) if x['status']==status] or ['暂无。']
lines+=['','## 暂时不能整理',''];lines+=['- '+x['source_id']+'：'+x['reason'] for x in data['blocked']] or ['暂无。']
lines+=['','附件另见[[90-Logs/附件转换/当前状态.md]]。图片仍未读，随手放音频仅登记；需要转写的录音只放会议/待处理。','']
body='\n'.join(lines);path='90-Logs/资料整理/未整理.md';old=c.tool('read_note',path=path)
before=None if old.startswith('Note not found:') else old.split('\n\n---\n\n',1)[1]
if before!=body:c.tool('write_note',path=path,content=body)
assert c.tool('read_note',path=path).split('\n\n---\n\n',1)[1]==body
print(json.dumps({'pending':len(data['pending']),'deferred':len(data.get('deferred',[])),'readback':True,'path':path}))
