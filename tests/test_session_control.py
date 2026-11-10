import time
import unittest
from datetime import timedelta
from sqlalchemy import select
from portal.models import LoginSession, UserAccount, now
from portal.security import sign, digest
from portal.session_control import SessionRevocation, PATH, PURPOSE
import test_security_access as fixtures

class SessionControlTests(unittest.TestCase):
    setUp=fixtures.SecurityAccessTests.setUp
    tearDown=fixtures.SecurityAccessTests.tearDown
    events=fixtures.SecurityAccessTests.events
    # Reuse fixture only, not the parent test cases.
    def signed(self, **changes):
        payload={'operation_id':'op-unit','incident_id':'incident-unit','account_ref':'test_user',
            'test_run_id':'CIA-UNIT','actor_id':1,'exp':int(time.time())+30}
        payload.update(changes)
        return {'X-Session-Control':sign(payload,'e'*32,PURPOSE)}

    def enable(self):
        self.settings.session_control_enabled=True
        self.settings.session_control_secret='e'*32

    def test_revocation_denies_old_session_and_preserves_other_account_and_post(self):
        self.enable()
        with self.factory.begin() as db:
            db.add(LoginSession(user_id=1,session_token_hash=digest('admin-session'),
                csrf_token_hash=digest('csrf'),expires_at=now()+timedelta(hours=1)))
        self.client.cookies.set('portal_session','test-session')
        self.assertEqual(self.client.get('/api/posts').status_code,200)
        result=self.client.post(PATH,json={},headers=self.signed())
        self.assertEqual(result.status_code,200);self.assertEqual(result.json()['revoked_count'],1)
        self.assertEqual(self.client.get('/api/posts').status_code,401)
        self.client.cookies.set('portal_session','admin-session')
        self.assertEqual(self.client.get('/api/posts').status_code,200)
        from portal.content import Post
        with self.factory() as db:
            post=db.get(Post,701);self.assertEqual((post.title,post.content,post.version,post.deleted_at),('Fixture','Original',1,None))
            self.assertIsNotNone(db.scalar(select(LoginSession).where(LoginSession.user_id==2)).revoked_at)

    def test_receipt_retry_does_not_revoke_new_session(self):
        self.enable()
        first=self.client.post(PATH,json={},headers=self.signed()).json()
        with self.factory.begin() as db:
            db.add(LoginSession(user_id=2,session_token_hash=digest('new-session'),
                csrf_token_hash=digest('csrf'),expires_at=now()+timedelta(hours=1)))
        self.assertEqual(self.client.post(PATH,json={},headers=self.signed()).json(),first)
        self.client.cookies.set('portal_session','new-session')
        self.assertEqual(self.client.get('/api/posts').status_code,200)
        self.assertEqual(self.client.post(PATH,json={},headers=self.signed(operation_id='another')).status_code,409)

    def test_disabled_invalid_signature_expiry_and_nontrial_are_rejected(self):
        self.assertEqual(self.client.post(PATH,headers=self.signed()).status_code,403)
        self.enable()
        for headers in ({'X-Session-Control':'forged'},self.signed(exp=int(time.time())-1),
                        self.signed(account_ref='admin01'),self.signed(actor_id=True),self.signed(exp=int(time.time())+300)):
            self.assertEqual(self.client.post(PATH,headers=headers).status_code,403)
        with self.factory() as db:
            self.assertIsNone(db.scalar(select(LoginSession).where(LoginSession.user_id==2)).revoked_at)
            self.assertEqual(len(db.scalars(select(SessionRevocation)).all()),0)

    def test_service_independently_rejects_admin_trial_account_and_shared_secret(self):
        self.enable()
        with self.factory.begin() as db: db.get(UserAccount,2).role='admin'
        self.assertEqual(self.client.post(PATH,headers=self.signed()).status_code,403)
        self.settings.session_control_secret=self.settings.app_secret
        self.assertEqual(self.client.post(PATH,headers=self.signed()).status_code,503)


    def test_receipt_identity_change_is_rejected(self):
        self.enable()
        self.assertEqual(self.client.post(PATH,headers=self.signed()).status_code,200)
        self.assertEqual(self.client.post(PATH,headers=self.signed(test_run_id='OTHER')).status_code,409)
