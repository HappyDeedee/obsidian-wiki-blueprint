#!/usr/bin/env python3
"""Expose bounded, non-biometric operational status to the ordinary health Skill."""
import os
import datetime,json,pathlib,time
from kb_minutes import MCP
R=pathlib.Path('/var/lib/kb-confirm');V=pathlib.Path('/var/lib/kb-voiceprint');D=pathlib.Path('/srv/wiki/meeting/data')
def run():
 status=json.loads((V/'watch-status.json').read_text());now=time.time();alerts=[];rows=[]
 for h,x in status.get('meetings',{}).items():
  if x['state']=='EXCLUDED_TEST_OR_OUTSIDE_MEETINGS':continue
  m=D/(h+'.obsidian.json');age=(now-m.stat().st_mtime)/3600 if m.exists()else None
  ids=x.get('speakers',{});missing=x['state']!='COMPLETE'
  for s,item in ids.items():
   if item['state']=='CARD':
    p=R/'items'/(item['card']+'.json')
    card=json.loads(p.read_text())if p.exists()else{}
    if not card.get('evidence')or not card.get('assets')or not all((R/'assets'/a).is_file()for a in card.get('assets',[])):missing=True
  if age is not None and age>24 and missing:alerts.append(x['note_path']+'：超过24小时未完成声纹/有效确认卡；'+x.get('error',x['state']))
  rows.append('- [['+x['note_path']+']]：'+x['state']+'；发言人 '+str(len(ids))+' 项。')
 if now-status['at']>600:alerts.append('声纹状态超过10分钟未更新，不能判定已正常处理。')
 people=R/'people_state.json';ps=json.loads(people.read_text())if people.exists()else{}
 alerts+=['自动更新异常：'+x.get('stage',x.get('meeting','未知'))+'：'+x.get('error','未知')for x in status.get('errors',[])]
 lines=['---','kind: generated','type: 运维状态','---','# 声纹与人物页处理状态','','检查时间：'+datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(timespec='seconds')+'。','','## 超24小时告警','']+(alerts or ['无；仅代表本次程序检查通过，不代表身份已由主人确认。'])
 lines+=['','## 人物页更新','','状态：'+str(ps.get('status','尚未运行'))+'；待处理：'+str(ps.get('pending','未知'))+'。']
 lines+=['- '+x['name']+'：'+x['reason']for x in ps.get('blocked',[])]
 lines+=['','## 真实会议','']+rows+['','私有声音、向量、试听文件未写入知识库。本页为程序状态，不参与知识整理。']
 cfg=json.loads(pathlib.Path('/srv/wiki/stack/credentials.json').read_text());c=MCP(os.environ.get('WIKI_MCP_URL','https://wiki.example.invalid/mcp'),cfg['mcp_token']);c.open()
 try:
  if json.loads(c.tool('curation_status',{})).get('writer'):return
  path='90-Logs/运维说明/声纹处理状态.md';new='\n'.join(lines)+'\n';raw=c.tool('read_note',{'path':path,'limit':4000});old=None if raw.startswith('Note not found:')else c.read(path)
  if old!=new:c.tool('write_note',{'path':path,'content':new})
  assert c.read(path)==new
 finally:c.close()
if __name__=='__main__':run()
