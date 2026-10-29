"""Bounded, serial aliases refresh. Checkpoint reads; never publish a partial view."""
import datetime, hashlib, importlib.util, json, pathlib, re, time, urllib.parse
import yaml


def listing(c):
    rows = {}
    # Any YAML aliases key necessarily contains this literal ASCII word.
    # Read candidate pages, not every one of the thousands of unrelated notes.
    for folder in ('20-Wiki', '30-Projects'):
        value = c.tool('search_notes', terms=['aliases:'], folder=folder, limit=500)
        if 'catching up' in value or 'may be incomplete' in value:
            raise RuntimeError('Index incomplete; keep previous aliases view')
        marker = re.search(r'\[SearchPage (\{.*\})\]', value)
        if not marker or json.loads(marker[1]).get('next_offset') is not None:
            raise RuntimeError('Aliases candidate search incomplete; keep previous view')
        for line in value.splitlines():
            m = re.match(r'^- (\S+) .*?\(obsidian://open\?vault=[^&]+&file=([^\n)]+)\)', line)
            if m:
                p = urllib.parse.unquote(m[2]).removesuffix('.md') + '.md'
                if p.startswith(('20-Wiki/', '30-Projects/')) and p != '20-Wiki/别名表.md':
                    rows[p] = m[1]
    return rows


def save(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    tmp.chmod(0o600)
    tmp.replace(path)


def refresh(c, root, state, held=None, publish=False, batch_size=40):
    spec = importlib.util.spec_from_file_location('views', root / 'wiki-generated-views.py')
    views = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(views)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    cache_path = state / 'aliases-incremental-cache.json'
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {'pages': {}}
    pages = cache['pages']
    current = listing(c)
    for p in set(pages) - set(current):
        del pages[p]
    # Re-read overlapping minutes, because list_notes renders mtime at minute precision.
    overlap = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M')
    pending = [p for p, stamp in current.items() if p not in pages or pages[p]['stamp'] != stamp or stamp >= overlap]
    changed = {}
    start = time.monotonic()
    for p in pending[:batch_size]:
        if time.monotonic() - start > 80:
            break
        body = views.read(c, p)
        if body is None:
            raise RuntimeError('Source disappeared: ' + p)
        aliases = views.metadata(body).get('aliases') or []
        if not isinstance(aliases, list) or any(not isinstance(x, str) for x in aliases):
            raise RuntimeError('Invalid aliases: ' + p)
        pages[p] = {'stamp': current[p], 'aliases': aliases, 'sha256': hashlib.sha256(body.encode()).hexdigest()}
        changed[p] = body
    if listing(c) != current:
        raise RuntimeError('Source listing changed; retry next run')
    save(cache_path, cache)
    remaining = len(pending) - len(changed)
    result = {'status': 'CHECKPOINT' if remaining else 'READY', 'read': len(changed), 'remaining': remaining, 'sources': len(current), 'changed': False}
    if remaining or not publish:
        return result
    texts = {p: '---\n' + yaml.safe_dump({'aliases': x['aliases']}, allow_unicode=True) + '---\n' for p, x in pages.items()}
    generated = views.generate(texts, 'aliases')
    if generated['errors']:
        raise RuntimeError(str(generated['errors']))
    lease = json.loads(pathlib.Path(held).read_text()) if held else json.loads(c.tool('curation_writer_acquire', owner='增量别名视图发布'))
    if lease.get('status') != 'ACQUIRED':
        return dict(result, status='BUSY')
    try:
        if listing(c) != current:
            raise RuntimeError('Source listing changed before publish')
        for p, text in changed.items():
            if views.read(c, p) != text:
                raise RuntimeError('Concurrent source change: ' + p)
        target = '20-Wiki/别名表.md'
        old = views.read(c, target)
        if old != generated['body']:
            backup = state / 'generated-view-backups'
            backup.mkdir(mode=0o700, exist_ok=True)
            dest = backup / ('aliases-incremental-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
            save(dest, {'path': target, 'body': old})
            answer = c.tool('write_note', path=target, content=generated['body'])
            if 'saved' not in answer.lower():
                raise RuntimeError('Write rejected: ' + answer[:200])
        if views.read(c, target) != generated['body']:
            raise RuntimeError('Aliases readback mismatch')
        result.update(status='PASS', changed=old != generated['body'], count=generated['count'], target=target,
                      published_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
        save(state / 'aliases-last.json', result)
        return result
    finally:
        if not held:
            c.tool('curation_writer_release', token=lease['token'])
