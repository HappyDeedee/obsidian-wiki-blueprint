"""One confirmed vocabulary, shared by extraction, minutes and card admission.

Pure functions: no network, no writes, no source rewriting. A dictionary is data,
not an instruction channel. Ambiguous aliases never select a sense implicitly.
"""
import copy
import hashlib
import json
import re
import unicodedata

PATH = '20-Wiki/综合/已确认词典.md'


def normalized(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value))).casefold()


def validate(doc):
    if doc.get('schema') != 1 or not isinstance(doc.get('entries'), list):
        raise ValueError('dictionary_schema')
    ids=set()
    for e in doc['entries']:
        if e['id'] in ids or not e.get('canonical') or not e.get('sources'):
            raise ValueError('dictionary_identity_or_provenance')
        ids.add(e['id'])
        if e.get('confirmation') != 'owner_confirmed' or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', e.get('confirmed_date','')):
            raise ValueError('dictionary_unconfirmed_entry')
        if e.get('kind') not in {'term','person','product','project','entity'}:
            raise ValueError('dictionary_kind')
        for source in e['sources']:
            if not source.get('path') or not source.get('quote'):
                raise ValueError('dictionary_empty_source')
        for f in ('aliases','typos'):
            if not isinstance(e.get(f,[]),list) or not all(isinstance(s,str)and s for s in e.get(f,[])):
                raise ValueError('dictionary_alias_schema')
    return doc


def from_markdown(text):
    matches=re.findall(r'^```json\s*\n(.*?)\n```\s*$',text,re.S|re.M)
    if len(matches)!=1:raise ValueError('dictionary_json_block')
    return validate(json.loads(matches[0]))


def digest(doc):
    return hashlib.sha256(json.dumps(validate(doc),ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def lookup(doc, token, *, sense=None, domain=None):
    key=normalized(token)
    return [e for e in validate(doc)['entries'] if e.get('active',True)
            and (not sense or e.get('sense')==sense) and (not domain or e.get('domain')==domain)
            and key in {normalized(x)for x in [e['canonical'],*e.get('aliases',[]),*e.get('typos',[])]}]


def spelling_map(doc):
    out={}
    for e in validate(doc)['entries']:
        if not e.get('active',True):continue
        for typo in e.get('typos',[]):
            hits=lookup(doc,typo)
            if len(hits)==1 and typo!=e['canonical']:out[typo]=e
    return out


def normalize_prose(text, doc, *, markdown=False):
    """Replace only confirmed typos, never valid aliases or wikilink targets.

    Offsets are against the input. Protected YAML/code/links/anchors are retained.
    All replacements retain literal before/after and the confirmation provenance.
    """
    mapping=spelling_map(doc)
    if not mapping:return text,[]
    protected=[]
    if markdown:
        if re.match(r'^---\r?\n',text):
            match=re.match(r'^---\r?\n.*?\r?\n---(?:\r?\n|$)',text,re.S)
            if not match:raise ValueError('unclosed_frontmatter')
            protected.append((0,match.end()))
        for pattern in [r'(?ms)^```.*?^```[^\n]*',r'\[\[.*?\]\]',r'!\[[^\]]*\]\([^\n]*?\)',
                        r'\[[^\]]*\]\([^\n]*?\)',r'`[^`\n]*`',r'\^[\w-]+',r'<!--.*?-->']:
            protected.extend((m.start(),m.end())for m in re.finditer(pattern,text,re.S))
    regex=re.compile('|'.join(re.escape(x)for x in sorted(mapping,key=len,reverse=True)))
    edits=[]
    def sub(m):
        if any(m.start()<end and m.end()>start for start,end in protected):return m[0]
        # A new compound such as 术语MAX is a new identity/version question.
        if (m.start() and text[m.start()-1].isascii() and text[m.start()-1].isalnum())or(m.end()<len(text)and text[m.end()].isascii()and text[m.end()].isalnum()):return m[0]
        if m[0].isascii() and ((m.start() and text[m.start()-1].isalnum())or(m.end()<len(text)and text[m.end()].isalnum())):return m[0]
        e=mapping[m[0]];edits.append({'offset':m.start(),'old':m[0],'new':e['canonical'],'entry':e['id'],
                                    'confirmed_date':e['confirmed_date'],'sources':e['sources']})
        return e['canonical']
    return regex.sub(sub,text),edits


def question_decision(doc, question):
    """Semantic key is the normalized term plus confirmed sense, not qid/version.

    Existing free prose must first be reviewed into an explicit term and intent.
    Mentioning a known name does not answer a new voice or a deployment question.
    """
    token=question.get('term') or question.get('canonical_term')
    if not token:return {'action':'review','reason':'unstructured_question'}
    for record in doc.get('unknowns',[]):
        if normalized(token)==normalized(record['term']) and record.get('disposition')=='owner_does_not_know_do_not_repeat' and not question.get('conflict'):
            return {'action':'close','status_label':'主人已答复保留未知，自动关闭',
                    'semantic_key':'unknown:'+normalized(token),'reason':'owner_preserved_unknown',
                    'confirmed_sources':[{'path':record['source']}], 'definition_stays_unknown':True}
    hits=lookup(doc,token,sense=question.get('sense'),domain=question.get('domain'))
    if len(hits)!=1:return {'action':'ask','reason':'new_or_ambiguous_term','matches':[x['id']for x in hits]}
    e=hits[0]
    if question.get('conflict'):
        c=question['conflict']
        if not c.get('quote')or not c.get('source'):return {'action':'review','reason':'conflict_missing_literal_source'}
        return {'action':'ask','reason':'conflict','confirmed_entry':e['id'],'confirmed_sources':e['sources'],
                'conflict':c,'semantic_key':e['id']}
    intent=question.get('intent')
    if intent not in {'spelling','alias_equivalence','definition','confirmed_name_spelling'}:
        return {'action':'review','reason':'different_fact_not_answered_by_vocabulary','confirmed_entry':e['id']}
    if intent=='alias_equivalence':
        other=lookup(doc,question.get('other_term',''),sense=question.get('sense'),domain=question.get('domain'))
        if len(other)!=1 or other[0]['id']!=e['id']:return {'action':'ask','reason':'new_alias'}
    if intent=='definition' and not e.get('definition'):return {'action':'review','reason':'name_confirmation_is_not_definition'}
    return {'action':'close','status_label':'已确认过，自动关闭','semantic_key':e['id'],
            'confirmed_entry':e['id'],'confirmed_sources':e['sources']}


def close_card(card, decision, now):
    if decision.get('action')!='close' or card.get('status')not in {'open','snoozed'}:
        return copy.deepcopy(card)
    result=copy.deepcopy(card)
    result.setdefault('history',[]).append({'at':now,'op':'dictionary_auto_close','before_status':card['status'],
                                           'before_answer':card.get('answer'),'decision':decision})
    result.update(status='retired',status_label=decision.get('status_label','已确认过，自动关闭'),closed_at=now,
                  close_reason=decision,auto_closed=True)
    # Do not fabricate an owner answer or feed a new confirmation-response.
    return result


def hotwords(doc, limit=300):
    words={};skipped=[]
    for e in sorted(validate(doc)['entries'],key=lambda x:(x['kind']!='person',x['id'])):
        if not e.get('active',True):continue
        word=e['canonical']
        reason=None
        if len(word)>10:reason='超过10字符'
        elif not re.fullmatch(r'[\u4e00-\u9fffA-Za-z]+',word):reason='含标点、空白、数字或非中英字符'
        elif word not in words and len(words)>=limit:reason='超过300词上限'
        if reason:skipped.append({'entry':e['id'],'word':word,'reason':reason})
        else:words[word]=2
    return words,skipped


def identity_map(voices, domain_confirmations):
    """Key domain grants by confirmation card, not hardcoded person names."""
    out={};blocked=[]
    for vid,x in voices.items():
        if x.get('status')!='name':continue
        grant=domain_confirmations.get(x.get('card'),{})
        if grant.get('confirmed_by')!='owner' or not grant.get('domain') or not grant.get('source') or grant.get('confirmed_name')!=x.get('name'):
            blocked.append({'voice':vid,'reason':'domain_unconfirmed'});continue
        name=(x.get('name')or'').strip()
        if not name or '/'in name or '\\'in name or name in {'.','..'}:raise ValueError('unsafe_person_name')
        for m in x.get('members',[]):
            key=m['meeting']+'#'+str(m['speaker'])
            record={'name':name,'role':x.get('role'),'domain':grant['domain'],'card':x['card'],
                    'confirmed_at':x.get('confirmed_at'),'voice':vid,'meeting':m['meeting'],'speaker':str(m['speaker'])}
            if key in out and out[key]['name']!=name:raise ValueError('conflicting_confirmed_identities')
            out[key]=record
    return out,blocked


def identity_delta(previous,current):
    removed=[];added=[]
    for key in sorted(set(previous)|set(current)):
        before=previous.get(key);after=current.get(key)
        changed=before and after and any(before.get(f)!=after.get(f)for f in ['name','domain','role','card'])
        if before and (not after or changed):removed.append({'key':key,**before})
        if after and (not before or changed):added.append({'key':key,**after})
    return {'remove':removed,'add':added,'affected':sorted({x['name']for x in removed+added})}
