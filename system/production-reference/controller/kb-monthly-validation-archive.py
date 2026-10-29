"""Archive actual monthly answers as logs, never as knowledge or truth scores."""
import json,hashlib
def archive(c,item,preview=False,prefix=''):
    if not item:return {'status':'NO_COMPLETED_MONTHLY_RUN'}
    run=item['run'];messages=item['messages']
    text='\n\n'.join(b['text']for m in messages for b in m.get('blocks',[])if b.get('kind')=='text'and isinstance(b.get('text'),str))
    if not text:return {'status':'NO_ANSWER','run_id':run['id']}
    month=run['createdAt'][:7];path=prefix+'90-Logs/验证集/'+month+'-自动验证-'+run['id']+'.md'
    body='---\nkind: generated\n---\n# 月度自动验证原始结果\n\n来源：Rakazo 实际运行 '+run['id']+'。开始 '+str(run.get('startedAt'))+'；完成 '+str(run.get('completedAt'))+'。\n\n以下是模型原始答复及自评，不是维护者独立核验或全库正确率。出现“未上线”“不存在”等断言时，须区分未核实与实际否定证据；搜索意外露出参考答案也要记录。精确耗时以服务器运行时间为准。\n\n'+text+'\n'
    if preview:return {'status':'WOULD_ARCHIVE','path':path,'run_id':run['id'],'sha256':hashlib.sha256(body.encode()).hexdigest()}
    old=c.tool('read_note',path=path)
    if old.startswith('Note not found:'):
        c.tool('write_note',path=path,content=body)
    else:
        oldbody=old.split('\n\n---\n\n',1)[1]
        if oldbody!=body:raise ValueError('Existing monthly archive differs; do not overwrite')
    saved=c.tool('read_note',path=path)
    if saved.split('\n\n---\n\n',1)[1]!=body:raise ValueError('Monthly archive readback differs')
    return {'status':'PASS','path':path,'run_id':run['id'],'sha256':hashlib.sha256(body.encode()).hexdigest()}
