"""Trial-account session revocation. No password reset, account lock or IP blocking."""
import re
import time
from datetime import datetime
from fastapi import Request
from sqlalchemy import DateTime, Integer, String, JSON, select, update
from sqlalchemy.orm import Mapped, mapped_column
from portal.models import Base, UserAccount, Employee, LoginSession, now
from portal.security import unsign

PATH = '/internal/security/revoke-test-sessions'
PURPOSE = 'revoke-trial-sessions-v1'
ACCOUNT = 'test_user'

class SessionRevocation(Base):
    __tablename__ = 'trial_session_revocations'
    operation_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    incident_id: Mapped[str] = mapped_column(String(40), unique=True)
    account_ref: Mapped[str] = mapped_column(String(50))
    test_run_id: Mapped[str] = mapped_column(String(40))
    actor_id: Mapped[int] = mapped_column(Integer)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict] = mapped_column(JSON)


def register_session_control(app):
    from portal.main import ApiError
    settings = app.state.settings

    @app.post(PATH)
    def revoke(request: Request):
        if not settings.session_control_enabled or settings.environment != 'test':
            raise ApiError(403, 'SESSION_CONTROL_DISABLED', '시험 세션 폐기가 비활성화되어 있습니다.')
        secret = settings.session_control_secret
        if len(secret) < 32 or secret in {settings.app_secret, settings.proxy_secret,
                settings.internal_check_token, settings.test_context_secret}:
            raise ApiError(503, 'SESSION_CONTROL_CONFIG', '시험 세션 폐기 설정을 확인해 주세요.')
        payload = unsign(request.headers.get('x-session-control', ''), secret, PURPOSE)
        fields = {'operation_id','incident_id','account_ref','test_run_id','actor_id','exp'}
        if (not payload or payload['exp']>time.time()+60 or set(payload) != fields or payload.get('account_ref') != ACCOUNT
            or type(payload.get('actor_id')) is not int or payload['actor_id'] < 1
            or any(not isinstance(payload.get(k), str) or not re.fullmatch('[A-Za-z0-9_-]{1,40}', payload[k])
                   for k in ('operation_id','incident_id','test_run_id'))):
            raise ApiError(403, 'SESSION_CONTROL_DENIED', '승인된 시험 세션 폐기 요청이 필요합니다.')
        with app.state.sessions.begin() as db:
            account = db.scalar(select(UserAccount).where(UserAccount.username == ACCOUNT).with_for_update())
            employee = db.get(Employee, account.employee_id) if account else None
            if (account is None or account.role != 'user' or not account.is_active
                or employee is None or employee.employment_status != 'active'):
                raise ApiError(403, 'TRIAL_ACCOUNT_REQUIRED', '사용 가능한 일반 시험 계정만 폐기할 수 있습니다.')
            receipt = db.get(SessionRevocation, payload['operation_id'])
            if receipt:
                if any(getattr(receipt, k) != payload[k] for k in ('incident_id','account_ref','test_run_id','actor_id')):
                    raise ApiError(409, 'OPERATION_CONFLICT', '실행 식별자가 일치하지 않습니다.')
                return receipt.result
            if db.scalar(select(SessionRevocation).where(SessionRevocation.incident_id == payload['incident_id'])):
                raise ApiError(409, 'OPERATION_CONFLICT', '이미 처리한 사건입니다.')
            at = now()
            result = db.execute(update(LoginSession).where(LoginSession.user_id == account.user_id,
                LoginSession.revoked_at.is_(None), LoginSession.expires_at > at).values(revoked_at=at))
            output = {'operation_id':payload['operation_id'], 'incident_id':payload['incident_id'],
                'account_ref':ACCOUNT,'test_run_id':payload['test_run_id'], 'status':'succeeded',
                'revoked_count':result.rowcount, 'completed_at':at.isoformat()}
            db.add(SessionRevocation(**{k:payload[k] for k in ('operation_id','incident_id','account_ref','test_run_id','actor_id')},
                completed_at=at, result=output))
            app.state.mark(request, 'trial_sessions_revoked')
        return output
