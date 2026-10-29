"""Bounded CouchDB snapshot staging with catch-up while ordinary writes continue."""
import gzip,hashlib,json,pathlib,sqlite3,time,urllib.parse

def export_documents(get_json,snapshot,page_size=500,catchup_seconds=180):
    snapshot=pathlib.Path(snapshot)
    stage=snapshot/'.documents.sqlite'
    if stage.exists():raise RuntimeError('Snapshot staging file already exists')
    stage.touch(mode=0o600)
    db=sqlite3.connect(stage)
    db.execute('PRAGMA cache_size=-8192')
    db.execute('CREATE TABLE docs (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
    first=get_json('');sequence=first['update_seq'];last=None;changes=0;pages=0
    def save(doc):
        if not doc.get('_id') or not doc.get('_rev'):raise RuntimeError('Backup document identity missing')
        if doc.get('_deleted'):db.execute('DELETE FROM docs WHERE id=?',(doc['_id'],))
        else:db.execute('INSERT OR REPLACE INTO docs VALUES (?,?)',(doc['_id'],json.dumps(doc,ensure_ascii=False,separators=(',',':'))))
    try:
        while True:
            query={'include_docs':'true','attachments':'true','limit':page_size}
            if last is not None:query.update({'startkey':json.dumps(last),'limit':page_size+1})
            rows=get_json('_all_docs?'+urllib.parse.urlencode(query)).get('rows',[])
            if last is not None:rows=[r for r in rows if r['id']!=last][:page_size]
            for row in rows:save(row['doc'])
            db.commit()
            if not rows or len(rows)<page_size:break
            last=rows[-1]['id']
        deadline=time.monotonic()+catchup_seconds
        while True:
            if time.monotonic()>deadline:raise RuntimeError('Backup catch-up did not settle within its time limit; snapshot incomplete')
            before_delta=get_json('')
            query={'since':sequence,'include_docs':'true','attachments':'true','limit':page_size}
            batch=get_json('_changes?'+urllib.parse.urlencode(query));pages+=1
            for row in batch['results']:
                if row.get('deleted'):db.execute('DELETE FROM docs WHERE id=?',(row['id'],))
                else:save(row['doc'])
                changes+=1
            db.commit();sequence=batch['last_seq']
            current=get_json('')
            if current.get('purge_seq')!=first.get('purge_seq'):raise RuntimeError('Database purged during backup; snapshot incomplete')
            # _changes.last_seq and database.update_seq are opaque and can encode
            # the same checkpoint differently. Compare database info to itself.
            if batch.get('pending',0)==0 and current['update_seq']==before_delta['update_seq']:
                sequence=current['update_seq'];break
        count=db.execute('SELECT count(*) FROM docs').fetchone()[0]
        if count!=current['doc_count']:raise RuntimeError('Snapshot document count differs from stable database checkpoint')
        paths=set();target=snapshot/'couchdb-documents.ndjson.gz'
        with gzip.open(target,'wt',encoding='utf-8') as out:
            for (body,) in db.execute('SELECT body FROM docs ORDER BY id'):
                out.write(body+'\n');doc=json.loads(body)
                if doc.get('type') in ['plain','newnote'] and not doc.get('deleted'):
                    path=doc.get('path','')
                    if path.lower().endswith('.md'):paths.add(path)
        with target.open('rb') as saved:digest=hashlib.file_digest(saved,'sha256').hexdigest()
        proof={'consistency_method':'paged scan followed by bounded _changes catch-up; stable update_seq and doc_count; purge_seq unchanged','initial_sequence':first['update_seq'],'catchup_changes':changes,'catchup_pages':pages,'archive_sha256':digest}
        return sequence,count,paths,proof
    finally:
        db.close();stage.unlink(missing_ok=True)
