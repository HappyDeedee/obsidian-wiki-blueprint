"""Pure continuation policy, separate from network and database actions."""
import re
import datetime
import json


TRANSIENT_FAILURE = re.compile(
    r"(?:\b(?:429|500|502|503|504|520|521|524)\b|"
    r"(?:temporarily|currently) unavailable|upstream[_ ]error|timeout|timed out|"
    r"connection reset|stream .*interrupt|HTTP/2 stream failed|upstream_stream_truncated|"
    r"response stream ended before completion|stream ended without finish_reason|^Request could not be completed\.?$|^terminated$|"
    r"service unavailable|rate limit)",
    re.I,
)
PROVIDER_RECOVERY_FAILURE = re.compile(r"unknown provider for model", re.I)
# Observed provider SSE `event: error` with an empty data payload. Keep this
# exact, bounded class separate from arbitrary malformed/local JSON failures.
STREAM_JSON_FAILURE = re.compile(r"^Unexpected end of JSON input$", re.I)
NONRETRYABLE_FAILURE = re.compile(
    r"(?:model not found|invalid_request_error|"
    r"unsupported model|invalid model|bad request|\b(?:400|401|403)\b)",
    re.I,
)


def writer_retry_time(writer, now):
    """Recheck temporary contention within 30 seconds; never acquire over a live lease."""
    if not writer:
        return now
    try:
        expiry = datetime.datetime.fromisoformat(writer['expires'].replace('Z', '+00:00')).timestamp()
    except (KeyError, TypeError, ValueError):
        raise ValueError('Invalid writer expiry; inspect before scheduling')
    return max(now, min(expiry + 5, now + 30))



def upstream_unavailable(error):
    """Known provider capacity/HTTP faults only; local auth/ACL/configuration holds."""
    error=str(error or '')
    if re.search(r'no (?:healthy|available).*oauth account|no available accounts?|account pool (?:exhausted|empty)|账号池.*(?:耗尽|无可用)|没有可用.*(?:OAuth|账号)',error,re.I):return True
    if re.search(r'\b(?:429|5\d\d)\b',error) and re.search(r'http|status|upstream|provider|api|request|response|error',error,re.I):return True
    if re.search(r'\b(?:401|403)\b',error):
        return bool(re.search(r'upstream|oauth|account pool|provider account',error,re.I)) and not bool(re.search(r'mcp|vault|permission denied|invalid api.?key|api.?key.*invalid|configuration|local credential',error,re.I))
    return False

def failure_class(latest):
    """Classify a terminal run before deciding whether to wake it again.

    This is deliberately conservative: only recognizable transport/service
    failures are retried automatically. The known unknown-provider relay error
    has a separate bounded recovery class; other configuration errors hold.
    """
    if not latest or latest.get('status') != 'failed':
        return 'none'
    error = str(latest.get('error') or '')
    if upstream_unavailable(error):
        return 'upstream_unavailable'
    if re.match(r'^(?:CURATION_OUTPUT_TRUNCATED:|SOURCE_RUNTIME_CAPACITY:|SOURCE_RESPONSE_FORMAT_INVALID:)',error):
        return 'source_capacity'
    if STREAM_JSON_FAILURE.fullmatch(error.strip()):
        return 'stream_json'
    if PROVIDER_RECOVERY_FAILURE.search(error):
        return 'provider_recovery'
    # Some relays wrap a temporary upstream outage in HTTP 400. Only a parsed,
    # explicitly typed upstream_error with a recognizable temporary message
    # overrides that status; malformed requests and unknown 400s still hold.
    try:
        payload = json.loads(error[error.index('{'):])
        detail = payload.get('error', payload)
        if (isinstance(detail, dict) and detail.get('type') == 'upstream_error'
                and TRANSIENT_FAILURE.search(str(detail.get('message', '')))):
            return 'transient'
    except (ValueError, TypeError, AttributeError):
        pass
    if NONRETRYABLE_FAILURE.search(error):
        return 'nonretryable'
    if TRANSIENT_FAILURE.search(error):
        return 'transient'
    return 'unknown'


def recovery_delay(attempts, now, kind):
    """Bound automatic recovery probes so a broken upstream cannot hot-loop."""
    if attempts not in (0, 1):
        raise ValueError('Approved retry budget exhausted')
    return now + (60, 300)[attempts]


def retry_limit_decision(state, latest, now):
    """Retry recognized upstream failures slowly; hold every other failure."""
    kind = failure_class(latest)
    if kind not in ('transient', 'stream_json'):
        return {'action': 'pause', 'failure_class': kind}
    attempts = int(state.get('recovery_attempts', 0))
    if attempts >= 2:
        return {'action': 'pause', 'failure_class': kind}
    return {
        'action': 'schedule',
        'failure_class': kind,
        'attempt': attempts + 1,
        'due': recovery_delay(attempts, now, kind),
    }


def checkpoint_position(text):
    values = []
    for key in ('next_index', 'current_part', 'next_offset'):
        match = re.search(r'^' + key + r':\s*(\d+)\s*$', text, re.M)
        if not match:
            raise ValueError('Missing numeric checkpoint: ' + key)
        values.append(int(match.group(1)))
    return values


def decide(snapshot, state, position, total, now):
    """Only completed batches with saved progress may immediately continue."""
    if not snapshot['routine']['active'] or not snapshot['routine']['continuous']:
        return {'action': 'idle', 'reason': 'routine_paused_or_not_managed'}
    if snapshot['active']:
        return {'action': 'idle', 'reason': 'chief_busy'}
    latest = snapshot.get('latest')
    latest_id = latest['id'] if latest else None
    if latest and latest['status'] not in ('completed', 'failed', 'cancelled'):
        return {'action': 'idle', 'reason': 'nonterminal_run'}
    previous = state.get('position', position)
    if position[0] < previous[0] or position[0] == previous[0] and tuple(position) < tuple(previous):
        return {'action': 'hold', 'reason': 'checkpoint_regressed'}
    if position[0] > total:
        return {'action': 'hold', 'reason': 'checkpoint_beyond_queue'}
    if latest_id != state.get('handled_run'):
        advanced = tuple(position) > tuple(previous)
        return {'action': 'observe_terminal', 'run': latest_id, 'advanced': advanced,
                'completed': latest and latest['status'] == 'completed',
                'verify_indices': list(range(previous[0], position[0])),
                'failure_class': failure_class(latest), 'writer_contention': latest.get('writer_contention') is True}
    if position[0] == total:
        return {'action': 'hold', 'reason': 'queue_exhausted_pending_final_audit'}
    if now < state.get('not_before', 0):
        return {'action': 'idle', 'reason': 'backoff'}
    if now < state.get('pending_until', 0):
        return {'action': 'idle', 'reason': 'wakeup_pending'}
    return {'action': 'schedule', 'expectedLatest': latest_id}


def terminal_state(state, decision, position, now):
    out = dict(state)
    if decision.get('writer_contention'):
        out.update(handled_run=decision['run'],position=position,pending_until=0,not_before=now+10)
        return out
    if decision.get('failure_class')=='upstream_unavailable':
        out.update(handled_run=decision['run'],position=position,pending_until=0,not_before=now+900)
        return out
    failures = 0 if decision['completed'] and decision['advanced'] else state.get('failures', 0) + 1
    no_progress = 0 if decision['advanced'] else state.get('no_progress', 0) + 1
    out.update(handled_run=decision['run'], position=position, failures=failures, no_progress=no_progress, pending_until=0)
    out['not_before'] = now + (10 if failures == 0 else min(1800, 300 * 2 ** min(failures - 1, 4)))
    return out
