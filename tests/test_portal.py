import json,time,unicodedata
from datetime import timedelta
import pytest
from sqlalchemy import func,select,update
from sqlalchemy.exc import IntegrityError,OperationalError
from portal.models import Employee,LoginSession,UserAccount,now
from portal.security import digest,sign,token,verify_password
from portal.seed import seed
from conftest import login

def events(settings):
    with open(settings.log_file,encoding='utf-8') as f:return [json.loads(line) for line in f]

def test_seed_idempotence(setup,password):
    _,factory,_,_=setup
    with factory() as db:
        original=db.scalar(select(UserAccount.password_hash).where(UserAccount.username=='test_user'))
        assert original!=password and verify_password(original,password)
    seed(factory,token(),token())
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Employee))==10
        assert db.scalar(select(func.count()).select_from(UserAccount))==2
        assert db.scalar(select(UserAccount.password_hash).where(UserAccount.username=='test_user'))==original

def test_success_search_logout(setup,password):
    client,factory,settings,_=setup
    response=login(client,password);assert response.status_code==200
    assert 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=lax' in response.headers['set-cookie']
    cookie=client.cookies.get('portal_session')
    with factory() as db:assert db.scalar(select(LoginSession.session_token_hash))==digest(cookie)
    r=client.get('/api/employees',params={'name':'이서연'})
    assert r.status_code==200 and r.json()['count']==1
    assert r.json()['items'][0]['email']=='emp002@portal.test'
    assert r.headers['cache-control']=='no-store'
    assert client.post('/api/auth/logout',headers={'X-CSRF-Token':response.json()['csrf_token']}).status_code==200
    client.cookies.set('portal_session',cookie)
    assert client.get('/api/employees?name=이서연').status_code==401
    assert [e['event_type'] for e in events(settings)].count('logout_success')==1

@pytest.mark.parametrize('username',['test_user','missing'])
def test_uniform_failure(setup,username):
    client,_,settings,_=setup;r=login(client,'incorrect',username)
    assert r.status_code==401 and r.json()['error_code']=='INVALID_CREDENTIALS'
    assert r.json()['message']=='아이디 또는 비밀번호를 확인해 주세요.'
    assert events(settings)[-1]['event_type']=='login_failed'
    if username=='missing':assert events(settings)[-1]['account_ref'].startswith('hmac:')

@pytest.mark.parametrize('body',[{}, {'username':'','password':'a'}, {'username':'a'*51,'password':'a'}, {'username':'test_user','password':'a'*129}, {'username':4,'password':'x'}, {'username':'a','password':'b','extra':'c'}])
def test_login_validation(client,body):
    csrf=client.get('/api/auth/context').json()['csrf_token']
    r=client.post('/api/auth/login',json=body,headers={'X-CSRF-Token':csrf})
    assert r.status_code==400 and set(r.json())<={'error_code','message','request_id','details'}

def test_csrf_media_type_and_parse(client):
    assert client.post('/api/auth/login',json={}).status_code==403
    csrf=client.get('/api/auth/context').json()['csrf_token']
    assert client.post('/api/auth/login',content='x',headers={'X-CSRF-Token':csrf,'Content-Type':'text/plain'}).status_code==415
    assert client.post('/api/auth/login',json={},headers={'X-CSRF-Token':csrf,'Origin':'https://other.example'}).status_code==403
    assert client.post('/api/auth/login',content='{',headers={'X-CSRF-Token':csrf,'Content-Type':'application/json'}).status_code==400

def test_size_limit(client):assert client.post('/api/auth/login',content='a'*8193).status_code==413

@pytest.mark.parametrize('name,count',[('이서연',1),(' 이서연 ',1),('없는직원',0),('이서',0),(unicodedata.normalize('NFD','이서연'),1)])
def test_search_rules(client,password,name,count):
    assert login(client,password).status_code==200
    r=client.get('/api/employees',params={'name':name});assert r.status_code==200 and r.json()['count']==count

@pytest.mark.parametrize('query',['','?name=','?name=%20%20','?name='+('a'*51),'?name=a&name=b'])
def test_search_validation(client,password,query):
    assert client.get('/api/employees'+query).status_code==401
    login(client,password);assert client.get('/api/employees'+query).status_code==400

def test_inactive_and_expired(setup,password):
    client,factory,_,_=setup;login(client,password)
    with factory.begin() as db:db.execute(update(LoginSession).values(created_at=now()-timedelta(hours=3),expires_at=now()-timedelta(seconds=1)))
    assert client.get('/api/employees?name=이서연').status_code==401
    login(client,password)
    with factory.begin() as db:db.execute(update(Employee).where(Employee.employee_code=='EMP002').values(employment_status='inactive'))
    assert client.get('/api/employees?name=이서연').status_code==401
    assert login(client,password).status_code==401

def test_csrf_rotation(setup,password):
    client,_,_,_=setup;old=login(client,password).json()['csrf_token'];new=client.get('/api/auth/context').json()['csrf_token']
    assert old!=new
    assert client.post('/api/auth/logout',headers={'X-CSRF-Token':old}).status_code==403
    assert client.get('/api/employees?name=이서연').status_code==200
    assert client.post('/api/auth/logout',headers={'X-CSRF-Token':new}).status_code==200
    assert client.post('/api/auth/logout',headers={'X-CSRF-Token':new}).status_code==401

def test_failed_count_reset(setup,password):
    client,factory,_,_=setup
    for _ in range(2):assert login(client,'wrong').status_code==401
    with factory() as db:assert db.scalar(select(UserAccount.failed_attempts).where(UserAccount.username=='test_user'))==2
    login(client,password)
    with factory() as db:assert db.scalar(select(UserAccount.failed_attempts).where(UserAccount.username=='test_user'))==0

def test_commit_failure(setup,password,monkeypatch):
    client,factory,settings,_=setup;csrf=client.get('/api/auth/context').json()['csrf_token']
    from sqlalchemy.orm import Session
    original=Session.flush
    def fail(self,*a,**k):
        if any(isinstance(x,LoginSession) for x in self.new):raise OperationalError('secret SQL',{},Exception('secret'))
        return original(self,*a,**k)
    monkeypatch.setattr(Session,'flush',fail)
    r=client.post('/api/auth/login',json={'username':'test_user','password':password},headers={'X-CSRF-Token':csrf})
    assert r.status_code==503 and not client.cookies.get('portal_session')
    assert events(settings)[-1]['event_type']=='database_error'
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(LoginSession))==0
        assert db.scalar(select(UserAccount.last_login_at).where(UserAccount.username=='test_user')) is None

def test_db_failure_and_liveness(setup,password,monkeypatch):
    client,factory,settings,_=setup;csrf=client.get('/api/auth/context').json()['csrf_token']
    def fail(*a,**kw):raise OperationalError('secret',{},Exception('secret'))
    monkeypatch.setattr(factory.kw['bind'],'connect',fail)
    r=client.post('/api/auth/login',json={'username':'test_user','password':password},headers={'X-CSRF-Token':csrf})
    assert r.status_code==503 and events(settings)[-1]['event_type']=='database_error'
    assert client.get('/health').status_code==200

def test_internal_check(setup):
    client,factory,settings,_=setup
    assert client.get('/internal/check/employee').status_code==401
    headers={'Authorization':'Bearer '+settings.internal_check_token}
    assert client.get('/internal/check/employee',headers=headers).status_code==200
    with factory.begin() as db:db.execute(update(Employee).where(Employee.employee_code=='EMP002').values(name='changed'))
    r=client.get('/internal/check/employee',headers=headers)
    assert r.status_code==503 and r.json()['error_code']=='CHECK_RESULT_MISMATCH'

def test_duplicates_limit_and_null(setup,password):
    client,factory,_,_=setup
    with factory.begin() as db:
        for i in range(11):db.add(Employee(employee_code=f'DUP{i:03}',name='동명이인',department='실습',position='사원',email=f'dup{i}@portal.test',extension=None))
    login(client,password);r=client.get('/api/employees?name=동명이인').json()
    assert r['count']==10 and all(x['extension'] is None for x in r['items'])
    assert r['items'][0]['email']=='dup0@portal.test' and r['items'][-1]['email']=='dup9@portal.test'

def test_schema_constraints(setup):
    _,factory,_,_=setup
    for values in [{'role':'superuser'},{'failed_attempts':-1}]:
        with pytest.raises(IntegrityError):
            with factory.begin() as db:db.execute(update(UserAccount).values(**values))
    with pytest.raises(IntegrityError):
        with factory.begin() as db:db.delete(db.scalar(select(Employee).where(Employee.employee_code=='EMP002')))

def test_ids_metadata_logs_metrics(setup,password):
    client,_,settings,reader=setup
    signed=sign({'test_run_id':'TEST-001','source_ref':'lab-client-01','exp':int(time.time())+300},settings.test_context_secret,'test')
    csrf=client.get('/api/auth/context').json()['csrf_token']
    headers={'X-CSRF-Token':csrf,'X-Test-Context':signed,'X-Request-ID':'a'*32,'X-Proxy-Key':settings.proxy_secret}
    r=client.post('/api/auth/login',json={'username':'test_user','password':password},headers=headers);assert r.headers['x-request-id']=='a'*32
    r=client.get('/api/employees?name=이서연',headers={'X-Request-ID':'b'*32,'X-Test-Context':'forged'});assert r.headers['x-request-id']!='b'*32
    ev=events(settings);assert ev[-2]['synthetic'] and ev[-2]['test_run_id']=='TEST-001'
    assert not ev[-1]['synthetic'] and ev[-1]['source_ref'] is None
    assert ev[-1]['db_duration_ms']>=0 and len(ev[-1]['trace_id'])==32
    assert len({e['event_id'] for e in ev})==len(ev)
    raw=open(settings.log_file,encoding='utf-8').read()
    for secret in [password,csrf,client.cookies.get('portal_session'),'이서연','name=']:assert secret not in raw
    data=reader.get_metrics_data();metrics=[m for r in data.resource_metrics for s in r.scope_metrics for m in s.metrics]
    assert {m.name for m in metrics}=={'portal.http.requests','portal.http.duration','portal.db.duration'}
    assert all(set(p.attributes)<={'http.route','http.response.status_code'} for m in metrics for p in m.data.data_points)

def test_five_signed_failures(setup):
    client,_,settings,_=setup;csrf=client.get('/api/auth/context').json()['csrf_token']
    signed=sign({'test_run_id':'TEST-003','source_ref':'lab-client-01','exp':int(time.time())+300},settings.test_context_secret,'test')
    for _ in range(5):assert client.post('/api/auth/login',json={'username':'test_user','password':'wrong'},headers={'X-CSRF-Token':csrf,'X-Test-Context':signed}).status_code==401
    failures=[e for e in events(settings) if e['event_type']=='login_failed']
    assert len(failures)==5 and len({e['event_id'] for e in failures})==5
    assert all(e['source_ref']=='lab-client-01' and e['test_run_id']=='TEST-003' for e in failures)
