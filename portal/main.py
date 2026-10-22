import hmac
import json
import re
import time
import unicodedata
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException
from portal.config import Settings
from portal.db import build_engine, observation, session_factory
from portal.models import Employee, LoginSession, UserAccount, now
from portal.security import DUMMY_HASH, digest, sign, token, unknown_account, unsign, verify_password
from portal.telemetry import Telemetry

ROOT=Path(__file__).resolve().parent.parent
ROUTES={'/api/auth/context','/api/auth/login','/api/auth/logout','/api/employees','/health','/internal/check/employee'}

class ApiError(Exception):
    def __init__(self,status,code,message,event='request_rejected',details=None):
        self.status,self.code,self.message,self.event,self.details=status,code,message,event,details

def invalid(field):
    raise ApiError(400,'VALIDATION_ERROR','입력값을 확인해 주세요.','input_validation_failed',[{'field':field,'reason':'INVALID_VALUE'}])

def user_json(account):
    return {'user_id':account.user_id,'employee_id':account.employee_id,'username':account.username,'role':account.role}

def search_employees(db,name):
    return db.scalars(select(Employee).where(Employee.name==name,Employee.employment_status=='active').order_by(Employee.employee_code).limit(10)).all()

def create_app(settings=None,engine=None,metric_readers=None):
    settings=settings or Settings()
    engine=engine or build_engine(settings.database_url)
    factory=session_factory(engine)
    telemetry=Telemetry(settings,metric_readers)
    @asynccontextmanager
    async def lifespan(app):
        yield
        telemetry.shutdown()
        engine.dispose()
    app=FastAPI(title='Schutz Portal',version='1.0.0',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.engine=engine
    app.state.sessions=factory
    app.state.telemetry=telemetry

    def mark(request,event,reason=None,**fields):
        request.state.obs.update(event_type=event,reason_code=reason,**fields)

    def error_response(request,error):
        mark(request,error.event,error.code)
        body={'error_code':error.code,'message':error.message,'request_id':request.state.obs['request_id']}
        if error.details is not None: body['details']=error.details
        response=JSONResponse(body,status_code=error.status)
        if error.code=='AUTH_REQUIRED': response.delete_cookie(settings.session_cookie_name,path='/')
        return response

    @app.exception_handler(ApiError)
    async def api_error(request,error): return error_response(request,error)
    @app.exception_handler(RequestValidationError)
    async def validation_error(request,error):
        return error_response(request,ApiError(400,'VALIDATION_ERROR','입력값을 확인해 주세요.','input_validation_failed'))
    @app.exception_handler(SQLAlchemyError)
    async def db_error(request,error):
        return error_response(request,ApiError(503,'DATABASE_UNAVAILABLE','일시적으로 이용할 수 없습니다.','database_error'))
    @app.exception_handler(HTTPException)
    async def http_error(request,error):
        code={404:'NOT_FOUND',405:'METHOD_NOT_ALLOWED'}.get(error.status_code,'REQUEST_REJECTED')
        return error_response(request,ApiError(error.status_code,code,'요청을 처리할 수 없습니다.'))

    @app.middleware('http')
    async def boundary(request,call_next):
        started=time.perf_counter()
        incoming=request.headers.get('x-request-id','')
        trusted=hmac.compare_digest(request.headers.get('x-proxy-key',''),settings.proxy_secret)
        rid=incoming if trusted and re.fullmatch('[a-f0-9]{32}',incoming) else uuid.uuid4().hex
        state={'event_id':str(uuid.uuid4()),'service':'portal-api','environment':settings.environment,
            'event_type':'request_completed','request_id':rid,'route':request.url.path if request.url.path in ROUTES else 'other',
            'db_duration_ms':None,'synthetic':False,'test_run_id':None,'source_ref':None,'account_ref':None,'reason_code':None,'result_count':None}
        metadata=unsign(request.headers.get('x-test-context',''),settings.test_context_secret,'test')
        if metadata and all(isinstance(metadata.get(k),str) and re.fullmatch('[A-Za-z0-9_-]{1,40}',metadata[k]) for k in ['test_run_id','source_ref']):
            state.update(synthetic=True,test_run_id=metadata['test_run_id'],source_ref=metadata['source_ref'])
        request.state.obs=state
        ctx=observation.set(state)
        # New real SDK span; never trust arbitrary incoming trace headers.
        with telemetry.tracer.start_as_current_span(state['route'],record_exception=False,set_status_on_exception=False) as span:
            sc=span.get_span_context()
            state.update(trace_id=f'{sc.trace_id:032x}' if sc.is_valid else None,span_id=f'{sc.span_id:016x}' if sc.is_valid else None)
            try:
                size=0
                chunks=[]
                if request.method in {'POST','PUT','PATCH','DELETE'}:
                    async for chunk in request.stream():
                        size+=len(chunk)
                        if size>8192:
                            raise ApiError(413,'PAYLOAD_TOO_LARGE','요청 크기를 초과했습니다.')
                        chunks.append(chunk)
                    request.state.raw_body=b''.join(chunks)
                response=await call_next(request)
            except ApiError as error:
                response=error_response(request,error)
            except SQLAlchemyError:
                response=error_response(request,ApiError(503,'DATABASE_UNAVAILABLE','일시적으로 이용할 수 없습니다.','database_error'))
            except Exception:
                # Do not stringify exceptions: driver/parser messages can contain secrets.
                response=error_response(request,ApiError(500,'INTERNAL_ERROR','처리 중 오류가 발생했습니다.','application_error'))
            response.headers['X-Request-ID']=rid
            response.headers['Cache-Control']='no-store'
            response.headers['X-Content-Type-Options']='nosniff'
            response.headers['Referrer-Policy']='no-referrer'
            response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
            state.update(occurred_at=now().isoformat().replace('+00:00','Z'),status_code=response.status_code,
                result='success' if response.status_code<400 else 'failure',duration_ms=round((time.perf_counter()-started)*1000,3))
            if state['db_duration_ms'] is not None: state['db_duration_ms']=round(state['db_duration_ms'],3)
            telemetry.record(state)
        observation.reset(ctx)
        return response

    def cookie(response,name,value,max_age):
        response.set_cookie(name,value,max_age=max_age,httponly=True,secure=settings.cookie_secure,samesite='lax',path='/')

    def authenticate(request,db,required=True,lock=False):
        value=request.cookies.get(settings.session_cookie_name,'')
        row=None
        if value and len(value)<=128:
            stmt=select(LoginSession,UserAccount,Employee).join(UserAccount,LoginSession.user_id==UserAccount.user_id).join(Employee,UserAccount.employee_id==Employee.employee_id).where(
                LoginSession.session_token_hash==digest(value),LoginSession.revoked_at.is_(None),LoginSession.expires_at>now(),UserAccount.is_active.is_(True),Employee.employment_status=='active')
            if lock: stmt=stmt.with_for_update()
            row=db.execute(stmt).first()
        if row is None and required:
            raise ApiError(401,'AUTH_REQUIRED','로그인이 필요합니다.')
        if row: request.state.obs['account_ref']=row[1].username if row[1].username in {'admin01','test_user'} else unknown_account(row[1].username,settings.app_secret)
        return row

    def csrf(request,expected_hash):
        supplied=request.headers.get('x-csrf-token','')
        origin=request.headers.get('origin')
        if origin is not None and origin!=settings.allowed_origin:
            raise ApiError(403,'CSRF_INVALID','요청 정보를 새로 확인해 주세요.','csrf_validation_failed')
        if not supplied or len(supplied)>128 or not isinstance(expected_hash,str) or not hmac.compare_digest(digest(supplied),expected_hash):
            raise ApiError(403,'CSRF_INVALID','요청 정보를 새로 확인해 주세요.','csrf_validation_failed')

    @app.get('/api/auth/context')
    def context(request:Request):
        csrf_value=token()
        # No session cookie => no DB needed to issue prelogin context.
        with factory.begin() as db:
            row=authenticate(request,db,required=False,lock=True)
            if row:
                row[0].csrf_token_hash=digest(csrf_value)
                response=JSONResponse({'user':user_json(row[1]),'csrf_token':csrf_value})
            else:
                response=JSONResponse({'user':None,'csrf_token':csrf_value})
                cookie(response,settings.prelogin_cookie_name,sign({'hash':digest(csrf_value),'exp':int(time.time())+600},settings.app_secret,'csrf'),600)
                response.delete_cookie(settings.session_cookie_name,path='/')
        return response

    @app.post('/api/auth/login')
    def login(request:Request):
        prelogin=unsign(request.cookies.get(settings.prelogin_cookie_name,''),settings.app_secret,'csrf')
        csrf(request,prelogin.get('hash') if prelogin else None)
        if request.headers.get('content-type','').split(';')[0].lower()!='application/json':
            raise ApiError(415,'UNSUPPORTED_MEDIA_TYPE','JSON 형식으로 요청해 주세요.')
        try: body=json.loads(request.state.raw_body)
        except (ValueError,UnicodeError): invalid('body')
        if not isinstance(body,dict) or set(body)!={'username','password'}: invalid('body')
        if not isinstance(body['username'],str) or not 1<=len(body['username'].strip())<=50: invalid('username')
        if not isinstance(body['password'],str) or not 1<=len(body['password'])<=128: invalid('password')
        username=body['username'].strip()
        request.state.obs['account_ref']=username if username in {'admin01','test_user'} else unknown_account(username,settings.app_secret)
        success=False
        with factory.begin() as db:
            row=db.execute(select(UserAccount,Employee).join(Employee).where(UserAccount.username==username).with_for_update()).first()
            account,employee=row if row else (None,None)
            valid=verify_password(account.password_hash if account else DUMMY_HASH,body['password'])
            active=bool(account and account.is_active and employee.employment_status=='active')
            if active and valid:
                value,csrf_value=token(),token()
                account.failed_attempts=0
                account.last_login_at=now()
                previous=request.cookies.get(settings.session_cookie_name,'')
                if previous and len(previous)<=128:
                    db.execute(update(LoginSession).where(LoginSession.session_token_hash==digest(previous),LoginSession.revoked_at.is_(None)).values(revoked_at=now()))
                expires=now()+timedelta(seconds=settings.session_seconds)
                db.add(LoginSession(user_id=account.user_id,session_token_hash=digest(value),csrf_token_hash=digest(csrf_value),expires_at=expires))
                info=user_json(account)
                success=True
            elif active and not valid:
                account.failed_attempts+=1
        # Commit occurs before choosing success/failure event or setting a cookie.
        if not success:
            raise ApiError(401,'INVALID_CREDENTIALS','아이디 또는 비밀번호를 확인해 주세요.','login_failed')
        response=JSONResponse({'user':info,'csrf_token':csrf_value,'expires_at':expires.isoformat().replace('+00:00','Z')})
        cookie(response,settings.session_cookie_name,value,settings.session_seconds)
        response.delete_cookie(settings.prelogin_cookie_name,path='/')
        mark(request,'login_success')
        return response

    @app.post('/api/auth/logout')
    def logout(request:Request):
        with factory.begin() as db:
            session,account,employee=authenticate(request,db,lock=True)
            csrf(request,session.csrf_token_hash)
            session.revoked_at=now()
        response=JSONResponse({'status':'logged_out'})
        response.delete_cookie(settings.session_cookie_name,path='/')
        response.delete_cookie(settings.prelogin_cookie_name,path='/')
        mark(request,'logout_success')
        return response

    @app.get('/api/employees')
    def employees(request:Request):
        with factory.begin() as db:
            authenticate(request,db)
            names=request.query_params.getlist('name')
            if len(names)!=1: invalid('name')
            name=unicodedata.normalize('NFC',names[0].strip())
            if not 1<=len(name)<=50: invalid('name')
            if settings.environment=='test' and settings.search_db_delay_ms:
                before=time.perf_counter()
                time.sleep(settings.search_db_delay_ms/1000)
                request.state.obs['db_duration_ms']=(request.state.obs['db_duration_ms'] or 0)+(time.perf_counter()-before)*1000
                mark(request,'employee_search_completed',synthetic=True,test_run_id=settings.fault_run_id or 'db-delay')
            rows=search_employees(db,name)
            items=[{key:getattr(row,key) for key in ['employee_id','name','department','position','email','extension']} for row in rows]
        mark(request,'employee_search_completed',result_count=len(items))
        return {'items':items,'count':len(items)}

    @app.get('/health')
    def health(request:Request):
        mark(request,'health_check_completed')
        if settings.environment=='test' and settings.health_mode in {'error','slow'}:
            mark(request,'health_check_completed',synthetic=True,test_run_id=settings.fault_run_id or 'health-test')
            if settings.health_mode=='error':
                return JSONResponse({'status':'error','message':'시험용 오류'},status_code=500)
            time.sleep(3)
        return {'status':'ok','service':'portal-api'}

    @app.get('/internal/check/employee')
    def check(request:Request):
        if not hmac.compare_digest(request.headers.get('authorization',''),'Bearer '+settings.internal_check_token):
            raise ApiError(401,'AUTH_REQUIRED','서버 인증이 필요합니다.')
        with factory.begin() as db:
            expected=db.scalar(select(Employee).where(Employee.employee_code==settings.check_employee_code))
            if settings.environment=='test' and settings.search_db_delay_ms:
                before=time.perf_counter(); time.sleep(settings.search_db_delay_ms/1000)
                request.state.obs['db_duration_ms']=(request.state.obs['db_duration_ms'] or 0)+(time.perf_counter()-before)*1000
                mark(request,'health_check_completed',synthetic=True,test_run_id=settings.fault_run_id or 'db-delay')
            found=search_employees(db,settings.check_employee_name)
            if expected is None or expected.employee_id not in {row.employee_id for row in found}:
                raise ApiError(503,'CHECK_RESULT_MISMATCH','기능 점검에 실패했습니다.','health_check_completed')
            expected_id=expected.employee_id
        mark(request,'health_check_completed')
        return {'status':'ok','check_id':'employee_read','employee_id':expected_id}

    @app.get('/')
    def index(): return FileResponse(ROOT/'static/index.html')
    app.state.authenticate=authenticate
    app.state.csrf=csrf
    app.state.mark=mark
    app.state.settings=settings
    from portal.content import register_content
    register_content(app)
    app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
    return app
