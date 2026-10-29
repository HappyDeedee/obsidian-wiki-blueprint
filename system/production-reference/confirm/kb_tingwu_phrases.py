"""Daily, idempotent hotword resource updates. No scheduler or credential output.

Use inside the existing single meeting-intake process. Persist an attempt before
the API request: an ambiguous timeout is never blindly repeated or recreated.
"""
import datetime
import hashlib
import json
from kb_confirmed_dictionary import hotwords


class PhraseManager:
    def __init__(self, api, load_state, save_state, now=None, save_config=None):
        self.api,self.load,self.save=api,load_state,save_state
        self.save_config=save_config
        self.now=now or (lambda:datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))))

    @staticmethod
    def _data(result):
        return result.get('Data') if isinstance(result,dict) and isinstance(result.get('Data'),dict) else (result or {})

    @classmethod
    def _phrases(cls, result):
        data=cls._data(result)
        if not isinstance(data,dict) or not isinstance(data.get('Phrases'),list):
            raise ValueError('phrase_list_schema_unknown')
        if any(not isinstance(x,dict) or not x.get('PhraseId') or not isinstance(x.get('Name'),str) for x in data['Phrases']):
            raise ValueError('phrase_list_entry_unknown')
        return data['Phrases']

    @classmethod
    def _wordweights(cls, result, phrase_id):
        data=cls._data(result)
        if not isinstance(data,dict):raise ValueError('phrase_get_schema_unknown')
        # Official Get returns Data.Phrases[], unlike Create/Update's direct fields.
        if isinstance(data.get('Phrases'),list):
            matches=[x for x in data['Phrases'] if isinstance(x,dict) and x.get('PhraseId')==phrase_id]
            if len(matches)!=1:raise ValueError('phrase_get_identity_unknown')
            data=matches[0]
        elif data.get('PhraseId')!=phrase_id:
            raise ValueError('phrase_get_identity_unknown')
        value=data.get('WordWeights')
        if isinstance(value,str):
            try:value=json.loads(value)
            except json.JSONDecodeError:raise ValueError('phrase_wordweights_invalid')
        if not isinstance(value,dict):raise ValueError('phrase_wordweights_missing')
        return value

    def sync(self, dictionary):
        words,skipped=hotwords(dictionary)
        if not words:raise ValueError('no_eligible_hotwords')
        sha=hashlib.sha256(json.dumps(words,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        st=dict(self.load()or{});original=dict(st);today=self.now().date().isoformat()
        body={'Name':'PersonalKnowledge','WordWeights':json.dumps(words,ensure_ascii=False)}
        try:
            # Reconcile the single named resource before every create/update.
            listed=self.api('GET','/openapi/tingwu/v2/resources/phrases',None) or {}
            rows=self._phrases(listed)
            same=[x for x in rows if isinstance(x,dict) and x.get('Name')=='PersonalKnowledge' and x.get('PhraseId')]
            if len(same)>1:
                return {**st,'action':'blocked_reconcile_required','reason':'duplicate_named_phrase_resources_manual_review_required','skipped':skipped}
            phrase=same[0]['PhraseId'] if same else None
            if not phrase and st.get('phrase_id') and st.get('status')=='verified':
                st.update(read_status='missing_verified_phrase_manual_review',last_read_at=today)
                self.save(st)
                return {**st,'action':'blocked_reconcile_required','reason':'verified_phrase_not_in_list','skipped':skipped}
            if phrase:
                detail=self.api('GET','/openapi/tingwu/v2/resources/phrases/'+phrase,None) or {}
                actual=self._wordweights(detail,phrase)
                if actual==words:
                    st.update(phrase_id=phrase,sha256=sha,count=len(words),status='verified',
                              updated_day=st.get('updated_day',today),read_status='verified',last_read_at=today)
                    st.pop('pending_sha256',None);self.save(st)
                    return {**st,'action':'reused','skipped':skipped}
        except Exception as error:
            st=original
            st.update(read_status='failed',last_read_at=today,
                      last_read_error=error.get_error_code() if hasattr(error,'get_error_code') else type(error).__name__)
            self.save(st)
            return {**st,'action':'deferred_read_error','reason':'phrase_list_or_get_failed_no_write_attempt','skipped':skipped}
        if phrase and st.get('update_attempt_day')==today:
            return {**st,'action':'deferred','reason':'daily_update_limit_or_ambiguous_prior_attempt','skipped':skipped}
        if not phrase and st.get('create_attempt_day')==today:
            return {**st,'action':'deferred','reason':'daily_limit_or_ambiguous_prior_attempt','skipped':skipped}
        if not phrase and original.get('status') in {'attempting','unknown_or_denied','config_write_failed'} and original.get('last_write_error')!='Forbidden.NoPermission':
            return {**st,'action':'blocked_reconcile_required',
                    'reason':'prior_create_outcome_unknown_no_matching_resource_found','skipped':skipped}
        st.update(write_attempt_day=today,pending_sha256=sha,status='attempting')
        if phrase:
            st['phrase_id']=phrase;st['update_attempt_day']=today
        else:st['create_attempt_day']=today
        self.save(st)
        try:
            if phrase:
                result=self.api('PUT','/openapi/tingwu/v2/resources/phrases/'+phrase,body)
                action='updated'
            else:
                result=self.api('POST','/openapi/tingwu/v2/resources/phrases',body)
                data=self._data(result);phrase=data.get('PhraseId') or result.get('PhraseId');action='created'
            data=self._data(result)
            if data.get('Status',result.get('Status'))!='SUCCEEDED' or not phrase:raise ValueError('phrase_update_not_verified')
            if self.save_config:
                try:self.save_config(phrase)
                except Exception as error:
                    st.update(phrase_id=phrase,status='config_write_failed',last_write_error=type(error).__name__)
                    self.save(st);raise
        except Exception as error:
            st=original
            st.update(write_attempt_day=today,pending_sha256=sha,
                      status='verified' if original.get('status')=='verified' and original.get('phrase_id') else 'unknown_or_denied',
                      last_write_error=error.get_error_code() if hasattr(error,'get_error_code') else type(error).__name__)
            if phrase:st['update_attempt_day']=today
            else:st['create_attempt_day']=today
            self.save(st)
            raise
        st.update(phrase_id=phrase,sha256=sha,count=len(words),status='verified',updated_day=today,read_status='verified',last_read_at=today)
        st.pop('pending_sha256',None);st.pop('last_write_error',None);st.pop('last_read_error',None);self.save(st)
        return {**st,'action':action,'skipped':skipped}


def phrase_parameters(parameters, state):
    import copy
    out=copy.deepcopy(parameters)
    if state.get('status')=='verified' and state.get('phrase_id'):
        out.setdefault('Transcription',{})['PhraseId']=state['phrase_id']
    return out


RAM_POLICY={'Version':'1','Statement':[{'Effect':'Allow','Action':[
    'tingwu:CreateTranscriptionPhrases','tingwu:UpdateTranscriptionPhrases',
    'tingwu:ListTranscriptionPhrases','tingwu:GetTranscriptionPhrases'],'Resource':'*'}]}
