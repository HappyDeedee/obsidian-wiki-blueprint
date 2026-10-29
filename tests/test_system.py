import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from system import Center, backup, doctor, install, make_server, restore
from wiki import WikiError, note, read_json, save_json
from demo import TARGET, page


class SystemTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/'installation'
        install(self.root, demo=True)
        self.center = Center(self.root)

    def card(self):
        return next(x for x in self.center.listing()['items'] if x['id']=='web-demo-001')

    def answer(self):
        card = self.card()
        return self.center.act('resolve', {'id':card['id'], 'question_version':card['question_version'],
                                          'kind':'fix','text':'虚构预算仍未确定。'})

    def test_existing_installation_never_reset(self):
        before=(self.root/'private/access.json').read_bytes()
        with self.assertRaises(WikiError):install(self.root, demo=True)
        self.assertEqual(before,(self.root/'private/access.json').read_bytes())

    def test_received_is_not_ingested(self):
        result=self.answer()
        self.assertEqual(result['status'],'RECEIVED')
        self.assertFalse(result['adopted'])
        self.assertEqual(self.card()['ingestion']['state'],'received')
        self.assertIn('虚构预算',note(self.root/'vault',result['path']).read_text(encoding='utf-8'))
        with self.assertRaises(WikiError):self.answer()
        with self.assertRaises(WikiError):self.center.act('undo',{'id':'web-demo-001'})

    def test_only_final_corresponding_receipt_is_ingested(self):
        result=self.answer();wiki=self.center.vault
        a=next(read_json(p) for p in (wiki.state/'answers').glob('*.json')
               if read_json(p)['question_id']=='web-demo-001')
        tx=wiki.prepare(result['path'],[TARGET],operation='topic_repair')
        with self.assertRaisesRegex(WikiError,'NO_FINAL_RECEIPT'):
            wiki.resolve('web-demo-001',a['answer_id'],tx['token'])
        receipt=wiki.commit(tx['token'],{'quotes':['虚构预算仍未确定'],
            'updates':[{'path':TARGET,'content':page('预算仍未确定，保留原计划。',[result['path']])}]})
        wiki.resolve('web-demo-001',a['answer_id'],receipt['commit_key'])
        self.assertEqual(self.card()['ingestion']['state'],'ingested')
        self.assertEqual(self.card()['ingestion']['pages'],[TARGET])

    def test_stale_and_implicit_draft_rejected(self):
        for version,kind in [(0,'fix'),(self.card()['question_version'],'approve')]:
            with self.assertRaises(WikiError):
                self.center.act('resolve',{'id':'web-demo-001','question_version':version,'kind':kind,'text':'x'})

    def test_unknown_answer_does_not_create_fact(self):
        self.center.act('resolve',{'id':'web-demo-001','question_version':self.card()['question_version'],'kind':'forgot'})
        self.assertEqual(self.card()['ingestion']['state'],'not_adopted')
        self.assertEqual(self.center.vault._question('web-demo-001')['status'],'pending')
        self.center.act('undo',{'id':'web-demo-001'})
        self.assertEqual(self.card()['status'],'open')

    def test_demo_speaker_does_not_claim_voice_training(self):
        result=self.center.act('resolve',{'id':'speaker-demo-001','kind':'name','name':'虚构人物'})
        self.assertFalse(result['voice_model_called'])
        self.assertFalse(result['person_page_written'])

    def test_backup_restore_preserves_answers_and_access(self):
        self.answer();dest=Path(self.tmp.name)/'backup';new=Path(self.tmp.name)/'restored'
        backup(self.root,dest);restore(dest,new)
        self.assertEqual(self.card()['ingestion']['state'],next(x for x in Center(new).listing()['items'] if x['id']=='web-demo-001')['ingestion']['state'])
        self.assertEqual((self.root/'private/access.json').read_bytes(),(new/'private/access.json').read_bytes())
        with self.assertRaises(WikiError):restore(dest,new)
        self.assertFalse((new/'backup-manifest.json').exists())

    def test_corrupted_backup_and_nested_destination_rejected(self):
        dest=Path(self.tmp.name)/'backup';backup(self.root,dest)
        (dest/'private/cards.json').write_text('{}',encoding='utf-8')
        with self.assertRaisesRegex(WikiError,'manifest mismatch'):restore(dest,Path(self.tmp.name)/'bad')
        with self.assertRaises(WikiError):backup(self.root,self.root/'nested')

    def test_nested_link_is_not_followed_by_backup(self):
        outside=Path(self.tmp.name)/'outside';outside.mkdir();(outside/'private.txt').write_text('private',encoding='utf-8')
        link=self.root/'injected'
        try:link.symlink_to(outside,target_is_directory=True)
        except OSError:self.skipTest('Creating symlinks is unavailable for this account')
        try:
            with self.assertRaises(WikiError):backup(self.root,Path(self.tmp.name)/'backup')
        finally:link.unlink()

    def test_doctor_does_not_certify_production(self):
        result=doctor(self.root)
        self.assertEqual(result['production_readiness'],'NOT_CERTIFIED')
        self.assertTrue(result['checks']['ui_exists'])


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.root=Path(cls.tmp.name)/'home';install(cls.root,demo=True)
        cls.server=make_server(cls.root,0)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join(3);cls.tmp.cleanup()

    def req(self,method,path,body=None,headers=None):
        c=http.client.HTTPConnection('localhost',self.server.server_port,timeout=5)
        c.request(method,path,body=body,headers=headers or {})
        r=c.getresponse();result=(r.status,dict(r.getheaders()),r.read());c.close();return result

    def login(self):
        secret=read_json(self.root/'private/access.json')['password']
        status,h,_=self.req('POST','/login',urlencode({'password':secret}),{'Content-Type':'application/x-www-form-urlencoded'})
        self.assertEqual(status,303)
        self.assertIn('HttpOnly',h['Set-Cookie']);self.assertIn('SameSite=Strict',h['Set-Cookie'])
        return {'Cookie':h['Set-Cookie'].split(';')[0]}

    def test_anonymous_and_wrong_password_cannot_read(self):
        self.assertEqual(self.req('GET','/kbc?a=list')[0],401)
        self.assertEqual(self.req('POST','/login','password=wrong')[0],401)

    def test_authenticated_ui_and_config(self):
        h=self.login();status,_,body=self.req('GET','/kb/',headers=h)
        self.assertEqual(status,200);self.assertIn('KBC_CONFIG',body.decode())
        self.assertEqual(self.req('GET','/kb/config.js',headers=h)[0],200)

    def test_host_origin_and_csrf_are_enforced(self):
        self.assertEqual(self.req('GET','/kbc?a=list',headers={'Host':'outside.example.invalid'})[0],403)
        h=self.login();h.update({'Content-Type':'application/json'})
        self.assertEqual(self.req('POST','/kbc','{}',h)[0],403)
        h['Origin']='https://outside.example.invalid'
        self.assertEqual(self.req('POST','/kbc','{}',h)[0],403)

    def test_browser_origin_login_and_referrer_policy(self):
        self.assertEqual(self.req('GET','/kb/')[1]['Referrer-Policy'],'same-origin')
        secret=read_json(self.root/'private/access.json')['password']
        body=urlencode({'password':secret})
        self.assertEqual(self.req('POST','/login',body,{'Origin':'null'})[0],403)
        self.assertEqual(self.req('POST','/login',body,{'Origin':'http://localhost:'+str(self.server.server_port)})[0],303)

    def test_web_answer_then_received_not_adopted(self):
        h=self.login();_,_,js=self.req('GET','/config.js',headers=h)
        config=json.loads(js.decode().split('=',1)[1].rstrip(';'))
        h.update({'X-Wiki-CSRF':config['csrf'],'Content-Type':'application/json'})
        body=json.dumps({'a':'resolve','id':'web-demo-001','kind':'fix','text':'网页虚构答复。','question_version':1})
        status,_,data=self.req('POST','/kbc',body,h)
        self.assertEqual(status,200);self.assertEqual(json.loads(data)['status'],'RECEIVED')
        _,_,data=self.req('GET','/kbc?a=list',headers=h)
        item=next(x for x in json.loads(data)['items'] if x['id']=='web-demo-001')
        self.assertEqual(item['ingestion']['state'],'received')


if __name__=='__main__':unittest.main()
