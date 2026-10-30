"""Initialize private service inputs and inspect the real Tingwu profile offline.

No network calls, service activation, model requests or platform mutations.
"""
import argparse
import json
from pathlib import Path
import secrets
import shutil
import sys
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def modules():
    for name in ('confirm', 'meeting'):
        path = str(ROOT / 'system/production-reference' / name)
        if path not in sys.path:
            sys.path.insert(0, path)
    import meeting_analysis
    import kb_confirmed_dictionary
    import kb_dictionary_cache
    return meeting_analysis, kb_confirmed_dictionary, kb_dictionary_cache


def external(path):
    path = Path(path).expanduser().resolve()
    if path == ROOT or ROOT in path.parents:
        raise ValueError('private_output_must_be_outside_repository')
    return path


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    path.chmod(0o600)


def init(private_dir):
    target = external(private_dir)
    if target.exists():
        raise ValueError('target_exists_preserved_choose_new_private_directory')
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.wiki-services-', dir=target.parent))
    try:
        temporary.chmod(0o700)
        for rel in ('meeting/data', 'confirm', 'voiceprint'):
            (temporary / rel).mkdir(parents=True, mode=0o700)
        config = json.loads((ROOT / 'system/config/meeting.example.json').read_text(encoding='utf-8'))
        config['data_dir'] = str(target / 'meeting/data')
        config['phone_token'] = secrets.token_urlsafe(32)
        config['signing_secret'] = secrets.token_urlsafe(48)
        write_json(temporary / 'meeting/config.json', config)
        for rel, value in {
            'confirm/people-v2-seeds.json': {},
            'confirm/people-domains.json': {},
            'confirm/people_merge_state.json': {'profiles': {}, 'identities': {}},
            'confirm/cloud.json': {'mode': 'local'},
            'voiceprint/voices.json': {},
        }.items():
            write_json(temporary / rel, value)
        for rel in ('confirm/people.lock', 'confirm/.lock'):
            (temporary / rel).touch(mode=0o600)
        # This is the legacy production-reference key, not the local UI password.
        key = temporary / 'confirm/access.key'
        key.write_text(secrets.token_urlsafe(32), encoding='utf-8')
        key.chmod(0o600)
        write_json(temporary / 'installation.json', {
            'schema': 1, 'profile': 'service-inputs-only',
            'services_started': False, 'roles_registered': False,
            'person_workers_enabled': False, 'production_certified': False,
            'needs': ['own_cloud_credentials', 'own_https_route', 'custom_mcp_host',
                      'registered_service_roles', 'production_ui_auth_adapter', 'real_device_acceptance'],
        })
        temporary.rename(target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {'status': 'PRIVATE_INPUTS_CREATED', 'services_started': False,
            'secrets_printed': False, 'production_certified': False,
            'windows_acl_action': 'Restrict directory to the service owner on Windows; chmod is not an ACL.'}


def placeholder(value):
    return not isinstance(value, str) or not value or any(x in value for x in ('.invalid', 'REPLACE_', 'CONFIGURE_'))


def doctor(private_dir):
    base = external(private_dir)
    config = json.loads((base / 'meeting/config.json').read_text(encoding='utf-8'))
    blockers = []
    for field in ('public_base_url', 'phone_token', 'signing_secret', 'app_key',
                  'access_key_id', 'access_key_secret', 'obsidian_mcp_url', 'obsidian_mcp_token'):
        if placeholder(config.get(field)):
            blockers.append({'field': field, 'reason': 'missing_own_value'})
    for field in ('public_base_url', 'obsidian_mcp_url'):
        parsed = urlsplit(config.get(field, ''))
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            blockers.append({'field': field, 'reason': 'requires_https_without_embedded_credentials'})
    if config.get('listen_host') not in ('localhost', '127.0.0.1', '::1'):
        blockers.append({'field': 'listen_host', 'reason': 'bind_to_loopback_behind_own_tls_proxy'})
    if Path(config.get('data_dir', '')).resolve() != (base / 'meeting/data').resolve():
        blockers.append({'field': 'data_dir', 'reason': 'must_match_this_installation'})
    for field in ('phone_token', 'signing_secret'):
        if not isinstance(config.get(field), str) or len(config[field]) < 32:
            blockers.append({'field': field, 'reason': 'secret_too_short'})
    for name in ('people-v2-seeds.json', 'people-domains.json', 'people_merge_state.json'):
        path = base / 'confirm' / name
        try:
            if not isinstance(json.loads(path.read_text(encoding='utf-8')), dict):
                raise ValueError('not_object')
        except (OSError, ValueError):
            blockers.append({'field': name, 'reason': 'missing_or_invalid_state_object'})
    return {'status': 'BLOCKED_INPUTS' if blockers else 'INPUT_SHAPE_READY', 'blockers': blockers,
            'network_tested': False, 'mcp_host_tested': False, 'production_certified': False,
            'person_workers': 'Keep disabled until own identity and domain confirmations exist.'}


def profile(dictionary_path=None):
    analysis, dictionary, _ = modules()
    doc = dictionary.from_markdown(Path(dictionary_path).read_text(encoding='utf-8')) if dictionary_path else {'schema': 1, 'entries': [], 'unknowns': []}
    return analysis.analysis_parameters(dictionary=doc, phrase_state={})


def export_profile(output):
    target = external(output)
    if target.exists():
        raise ValueError('output_exists_preserved')
    analysis, _, _ = modules()
    target.mkdir(parents=True, mode=0o700)
    (target / 'tingwu-minutes.txt').write_text(analysis.MINUTES_PROMPT + '\n', encoding='utf-8')
    write_json(target / 'tingwu-parameters.json', profile())
    return {'status': 'EXPORTED_OFFLINE', 'prompt_name': analysis.PROMPT_NAME,
            'analysis_version': analysis.ANALYSIS_VERSION, 'cloud_requests': 0}


def cache_dictionary(private_dir, dictionary_path):
    base = external(private_dir)
    config = json.loads((base / 'meeting/config.json').read_text(encoding='utf-8'))
    data = Path(config['data_dir']).resolve()
    if data != (base / 'meeting/data').resolve():
        raise ValueError('data_dir_outside_this_installation')
    _, _, cache = modules()
    path = data / 'confirmed-dictionary.cache.json'
    original = Path(dictionary_path).read_text(encoding='utf-8')
    # The owner must supply the formal dictionary, not an AI-generated candidate list.
    value = cache.refresh(original, path)
    path.chmod(0o600)
    return {'status': 'CACHE_VALIDATED_OFFLINE', 'entries': len(value['dictionary']['entries']),
            'dictionary_sha256': value['dictionary_sha256'], 'cloud_requests': 0,
            'limitation': 'Schema/provenance fields checked; actual owner consent must be verified by the operator.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('init', 'doctor'):
        sub.add_parser(name).add_argument('--private-dir', required=True)
    export = sub.add_parser('export-profile')
    export.add_argument('--output', required=True)
    cache = sub.add_parser('cache-dictionary')
    cache.add_argument('--private-dir', required=True)
    cache.add_argument('--dictionary', required=True)
    sub.add_parser('parameters').add_argument('--dictionary')
    args = parser.parse_args()
    if args.command == 'init':result = init(args.private_dir)
    elif args.command == 'doctor':result = doctor(args.private_dir)
    elif args.command == 'export-profile':result = export_profile(args.output)
    elif args.command == 'cache-dictionary':result = cache_dictionary(args.private_dir, args.dictionary)
    else:result = profile(args.dictionary)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result.get('status') == 'BLOCKED_INPUTS' else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError):
        # File values, credential-bearing paths and cloud responses are never printed.
        print(json.dumps({'status': 'FAILED', 'reason': 'invalid_or_unavailable_private_input'}))
        raise SystemExit(1)
