import json
import tempfile
import time
import unittest
from datetime import timedelta
from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from portal.config import Settings
from portal.main import create_app
from portal.content import Post
from portal.models import Base, Employee, UserAccount, LoginSession, now
from portal.security import digest, sign


class SecurityAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine('sqlite:///'+str(Path(self.temp.name)/'test.db'),
                                    connect_args={'check_same_thread':False})
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine)
        self.log = Path(self.temp.name)/'events.jsonl'
        self.settings = Settings(_env_file=None, database_url=str(self.engine.url),
            app_secret='a'*32, proxy_secret='b'*32, internal_check_token='c'*32,
            test_context_secret='d'*32, log_file=str(self.log))
        with self.factory.begin() as db:
            for uid in [1,2]:
                db.add(Employee(employee_id=uid,employee_code='EMP00'+str(uid),name='보호정보'+str(uid),
                    department='test',position='test',email=str(uid)+'@example.invalid'))
                db.flush()
                db.add(UserAccount(user_id=uid,employee_id=uid,username='test_user' if uid==2 else 'admin01',
                                   password_hash='unused',role='user' if uid==2 else 'admin'))
            db.flush()
            db.add(LoginSession(user_id=2,session_token_hash=digest('test-session'),
                csrf_token_hash=digest('test-csrf'),expires_at=now()+timedelta(hours=1)))
            db.add(Post(id=701,author_id=1,title='Fixture',content='Original'))
        self.client=TestClient(create_app(self.settings,self.engine))
        self.client.__enter__()
        self.headers={'X-Test-Context':sign({'test_run_id':'CIA-UNIT','source_ref':'lab-a',
            'exp':int(time.time())+300},self.settings.test_context_secret,'test')}

    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.engine.dispose(); self.temp.cleanup()

    def events(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_private_employee_response_is_absent_and_denial_is_observable(self):
        response=self.client.get('/api/employees?name=SECRET-NAME',headers=self.headers)
        self.assertEqual(response.status_code,401)
        self.assertNotIn('example.invalid',response.text)
        event=self.events()[-1]
        self.assertEqual(event['event_type'],'authentication_required')
        self.assertEqual((event['method'],event['route']),('GET','/api/employees'))
        self.assertTrue(event['synthetic'])
        self.assertNotIn('SECRET-NAME',self.log.read_text())

    def test_nonowner_write_denial_preserves_entire_test_object(self):
        def snapshot():
            with self.factory() as db:
                row=db.get(Post,701)
                return (row.author_id,row.title,row.content,row.version,row.deleted_at)
        before=snapshot();self.client.cookies.set('portal_session','test-session')
        headers={**self.headers,'X-CSRF-Token':'test-csrf'}
        for method,body in [('PUT',{'version':1,'title':'Tampered','content':'DO-NOT-LOG'}),
                            ('DELETE',{'version':1})]:
            response=self.client.request(method,'/api/posts/701',json=body,headers=headers)
            self.assertEqual(response.status_code,403)
            self.assertEqual(snapshot(),before)
            event=self.events()[-1]
            self.assertEqual((event['event_type'],event['reason_code']),('authorization_denied','FORBIDDEN'))
            self.assertEqual(event['route'],'/api/posts/{post_id}')
            self.assertEqual(event['account_ref'],'test_user')
        self.assertNotIn('DO-NOT-LOG',self.log.read_text())

    def test_csrf_failure_is_not_mislabeled_as_owner_denial(self):
        self.client.cookies.set('portal_session','test-session')
        response=self.client.put('/api/posts/701',json={'version':1,'title':'x','content':'x'},headers=self.headers)
        self.assertEqual(response.status_code,403)
        self.assertEqual(self.events()[-1]['event_type'],'csrf_validation_failed')

    def test_forged_trial_metadata_is_not_accepted(self):
        self.client.get('/api/employees',headers={'X-Test-Context':'forged'})
        event=self.events()[-1]
        self.assertFalse(event['synthetic'])
        self.assertIsNone(event['source_ref'])

    def test_unknown_dynamic_path_is_not_recorded_raw(self):
        self.client.get('/api/posts/SECRET-PATH',headers=self.headers)
        self.assertEqual(self.events()[-1]['route'],'other')
        self.assertNotIn('SECRET-PATH',self.log.read_text())
