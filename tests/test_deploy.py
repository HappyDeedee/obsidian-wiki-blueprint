import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from deploy import init
from wiki import WikiError


class DeployTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'private'

    def test_credentials_unique_roles_and_no_author_connection(self):
        result=init(self.root,'https://my-wiki.example.invalid','我的Wiki')
        cfg=json.loads((self.root/'settings.private.json').read_text(encoding='utf-8'))
        self.assertFalse(result['services_started']);self.assertFalse(result['role_registry_installed'])
        self.assertEqual(cfg['MCP_PUBLIC_URL'],'https://my-wiki.example.invalid/mcp')
        self.assertEqual(cfg['VAULT_NAME'],'我的Wiki')
        roles=[json.loads(p.read_text(encoding='utf-8')) for p in self.root.glob('*.private.json') if p.name not in {'settings.private.json','mcp-identities.private.json'}]
        self.assertEqual(len({r['token'] for r in roles}),4)
        self.assertNotEqual(cfg['COUCHDB_ADMIN_PASSWORD'],cfg['LIVESYNC_PASSWORD'])
        registry=json.loads((self.root/'mcp-identities.private.json').read_text(encoding='utf-8'))
        for role in roles:
            row=next(r for r in registry if r['role']==role['role'])
            self.assertEqual(row['sha256'],hashlib.sha256(('Bearer '+role['token']).encode()).hexdigest())
        self.assertNotIn(cfg['MCP_READER_TOKEN'],json.dumps(result))

    def test_nonempty_directory_is_never_replaced(self):
        init(self.root,'https://my-wiki.example.invalid')
        before=(self.root/'stack.env').read_bytes()
        with self.assertRaises(WikiError):init(self.root,'https://my-wiki.example.invalid')
        self.assertEqual(before,(self.root/'stack.env').read_bytes())

    def test_url_credentials_path_and_non_https_rejected(self):
        for url in ['http://my-wiki.example.invalid','https'+'://u:p@my-wiki.example.invalid','https://my-wiki.example.invalid/subpath','https://my-wiki.example.invalid?q=x']:
            with self.subTest(url=url):
                with self.assertRaises(WikiError):init(self.root,url)


if __name__=='__main__':unittest.main()
