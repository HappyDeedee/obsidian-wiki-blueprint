"""Pure reconciliation plan; no writes until leased full-transcript curation.

Every identity change invalidates that meeting's claims as well as its link.
The executor must rebuild/curate affected people from remaining confirmed sources;
it must not move a line while leaving misattributed business claims behind.
"""
import hashlib,json
from kb_confirmed_dictionary import identity_map,identity_delta


def plan(previous, voices, domains):
    current,blocked=identity_map(voices,domains)
    changes=identity_delta(previous,current)
    by_name={}
    for x in current.values():by_name.setdefault(x['name'],set()).add(x['meeting'])
    edits=[]
    for name in changes['affected']:
        remaining=sorted(by_name.get(name,set()))
        edits.append({'name':name,'path':'20-Wiki/人物/'+name+'.md',
                      'action':'rebuild_from_confirmed_transcripts'if remaining else'preserve_and_mark_pending',
                      'meetings':remaining,'backup_required':True,'lease_required':True,
                      'remove_old_attributed_claims':True,'merge_by_meeting_not_speaker':True})
    key=hashlib.sha256(json.dumps(current,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return {'identity_sha256':key,'current':current,'delta':changes,'pages':edits,'blocked':blocked,
            'dictionary_refresh_required':bool(changes['affected']),
            'hotwords_refresh_required':bool(changes['affected']),
            'completion_contract':'Persist applied identity snapshot only after all page commits and readbacks; retry idempotently.'}


def substantive_person(text):
    import re
    sections=re.split(r'^## ',text,flags=re.M)
    topics=[]
    for part in sections:
        if part.startswith(('关注点与诉求\n','提供过的事实与数字\n','承诺与待办\n')):
            body=part.split('\n',1)[1]
            if re.search(r'\[\[.*?#\^sentence-\d+',body)and len(re.sub(r'\[\[.*?\]\]','',body))>=40:topics.append(body)
    return bool(topics)
