from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    database_url: str
    app_secret: str = Field(min_length=32)
    proxy_secret: str = Field(min_length=32)
    internal_check_token: str = Field(min_length=32)
    test_context_secret: str = Field(min_length=32)
    session_seconds: int = Field(default=7200, ge=60, le=86400)
    cookie_secure: bool = False  # local HTTP only; enable for HTTPS deployment
    allowed_origin: str = 'http://localhost:8080'
    environment: str = 'test'
    log_file: str | None = None
    otlp_metrics_endpoint: str | None = None
    check_employee_code: str = 'EMP002'
    check_employee_name: str = '이서연'
    health_mode: str = 'normal'
    search_db_delay_ms: int = Field(default=0, ge=0, le=3000)
    fault_run_id: str = ''
    session_cookie_name: str = 'portal_session'
    prelogin_cookie_name: str = 'portal_prelogin'
