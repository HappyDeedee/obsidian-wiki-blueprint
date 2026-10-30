"""Executable handoff checks without cloud requests or private source files."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('public_services', ROOT / 'tools/services.py')
services = importlib.util.module_from_spec(spec)
spec.loader.exec_module(services)


class ServicesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.private = self.base / 'private'

    def test_init_has_real_empty_state_and_unique_secrets(self):
        result = services.init(self.private)
        self.assertFalse(result['services_started'])
        other = self.base / 'other'
        services.init(other)
        cfg = json.loads((self.private / 'meeting/config.json').read_text())
        cfg2 = json.loads((other / 'meeting/config.json').read_text())
        self.assertNotEqual(cfg['phone_token'], cfg2['phone_token'])
        self.assertNotEqual(cfg['phone_token'], cfg['signing_secret'])
        self.assertEqual(Path(cfg['data_dir']).resolve(), (self.private / 'meeting/data').resolve())
        self.assertEqual(json.loads((self.private / 'confirm/people-v2-seeds.json').read_text()), {})
        self.assertTrue((self.private / 'confirm/people.lock').exists())

    def test_init_refuses_existing_target_without_changing_it(self):
        services.init(self.private)
        path = self.private / 'meeting/config.json'
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'target_exists_preserved'):
            services.init(self.private)
        self.assertEqual(before, path.read_bytes())

    def test_private_output_inside_repository_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'outside_repository'):
            services.init(ROOT / 'accidental-private')

    def test_doctor_blocks_placeholders_without_printing_secrets(self):
        services.init(self.private)
        result = services.doctor(self.private)
        self.assertEqual(result['status'], 'BLOCKED_INPUTS')
        cfg = json.loads((self.private / 'meeting/config.json').read_text())
        serialized = json.dumps(result)
        self.assertNotIn(cfg['phone_token'], serialized)
        self.assertNotIn(cfg['signing_secret'], serialized)
        self.assertFalse(result['production_certified'])
        cli = subprocess.run([sys.executable, str(ROOT / 'tools/services.py'), 'doctor', '--private-dir', str(self.private)], capture_output=True, text=True)
        self.assertEqual(cli.returncode, 2)
        self.assertEqual(json.loads(cli.stdout)['status'], 'BLOCKED_INPUTS')

    def test_shape_ready_does_not_claim_network_or_production_success(self):
        services.init(self.private)
        path = self.private / 'meeting/config.json'
        cfg = json.loads(path.read_text())
        cfg.update(public_base_url='https://meeting.example.org/capture', obsidian_mcp_url='https://wiki.example.org/mcp',
                   app_key='fake-app', access_key_id='fake-id', access_key_secret='fake-secret', obsidian_mcp_token='fake-own-token')
        path.write_text(json.dumps(cfg))
        result = services.doctor(self.private)
        self.assertEqual(result['status'], 'INPUT_SHAPE_READY')
        self.assertFalse(result['network_tested'])
        self.assertFalse(result['production_certified'])

    def test_tingwu_snapshot_and_full_prompt_match_executable_source(self):
        analysis, _, _ = services.modules()
        self.assertEqual((ROOT / 'prompts/tingwu-minutes.txt').read_text(encoding='utf-8'), analysis.MINUTES_PROMPT + '\n')
        snapshot = json.loads((ROOT / 'system/config/tingwu-parameters.example.json').read_text(encoding='utf-8'))
        self.assertEqual(snapshot, services.profile())
        prompt = snapshot['CustomPrompt']['Contents'][0]
        self.assertIn('{Transcription}', prompt['Prompt'])
        self.assertEqual(prompt['TransType'], 'sentence-chat')
        self.assertNotIn('PhraseId', snapshot['Transcription'])

    def test_export_never_overwrites_own_profile(self):
        target = self.base / 'profile'
        self.assertEqual(services.export_profile(target)['cloud_requests'], 0)
        with self.assertRaisesRegex(ValueError, 'preserved'):
            services.export_profile(target)

    def test_dictionary_cache_uses_private_data_path_and_checks_hash(self):
        services.init(self.private)
        dictionary = ROOT / 'system/config/confirmed-dictionary.empty.md'
        result = services.cache_dictionary(self.private, dictionary)
        self.assertEqual(result['entries'], 0)
        _, _, cache = services.modules()
        path = self.private / 'meeting/data/confirmed-dictionary.cache.json'
        self.assertIsNotNone(cache.read(path))
        value = json.loads(path.read_text(encoding='utf-8'))
        value['dictionary_sha256'] = 'invalid'
        path.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'invalid'):
            cache.read(path)

    def test_required_dependency_entries_are_present(self):
        for rel in ('format/format-convert.py', 'format/inbox-status.py', 'curation/temporal-inspect.mjs'):
            self.assertTrue((ROOT / 'system/production-reference' / rel).is_file(), rel)

    def test_no_private_cache_does_not_fall_back_to_other_installation(self):
        services.modules()
        from receiver import Tingwu
        provider = object.__new__(Tingwu)
        provider.dictionary_cache_path = self.base / 'absent-cache.json'
        with patch('meeting_analysis.read_dictionary_cache', side_effect=AssertionError('must not read global cache')):
            result = provider.confirmed_parameters()
        self.assertNotIn('PhraseId', result['Transcription'])

    def test_result_parser_reads_explicit_installation_cache(self):
        analysis, _, _ = services.modules()
        from test_receiver import RESULTS
        selected = self.base / 'selected-cache.json'
        with patch('meeting_analysis.read_dictionary_cache', return_value=None) as read:
            result = analysis.normalize_enhanced(RESULTS, 3, selected)
        read.assert_called_once_with(selected)
        self.assertEqual(result['minutes']['overview'], '预算仍需确认。')


class FormatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        path = ROOT / 'system/production-reference/format/format-convert.py'
        spec = importlib.util.spec_from_file_location('isolated_format', path)
        self.converter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.converter)

    def test_existing_output_and_original_are_preserved(self):
        source, output = self.base / 'source.txt', self.base / 'output.md'
        source.write_text('original source', encoding='utf-8')
        output.write_text('existing reviewed output', encoding='utf-8')
        with self.assertRaises(FileExistsError):
            self.converter.convert(source, output)
        self.assertEqual(output.read_text(), 'existing reviewed output')
        self.assertEqual(source.read_text(), 'original source')

    def test_real_markitdown_html_conversion_preserves_source_bytes(self):
        # CI installs the pinned engine. Absence is a failed setup, not a passing mock.
        source, output = self.base / 'source.html', self.base / 'output.md'
        source.write_text('<html><body><h1>Example topic</h1><p>Evidence stays literal.</p></body></html>', encoding='utf-8')
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        result = self.converter.convert(source, output)
        self.assertEqual(result['status'], 'TEXT_EXTRACTED')
        self.assertEqual(result['semantic_review'], 'NOT_PERFORMED')
        self.assertIn('Evidence stays literal.', output.read_text(encoding='utf-8'))
        self.assertEqual(digest, hashlib.sha256(source.read_bytes()).hexdigest())

    def test_empty_text_cannot_be_reported_as_success(self):
        source, output = self.base / 'empty.txt', self.base / 'output.md'
        source.write_bytes(b'')
        with self.assertRaises(ValueError):
            self.converter.convert(source, output)
        self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
