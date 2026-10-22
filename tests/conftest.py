import os,secrets,sys
from pathlib import Path
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from portal.config import Settings
from portal.db import build_engine,session_factory
from portal.main import create_app
from portal.seed import seed
@pytest.fixture(scope='session')
def password(): return secrets.token_urlsafe(18)
@pytest.fixture
def setup(tmp_path,password):
    url=os.getenv('TEST_DATABASE_URL')
    if url and not (make_url(url).database or '').endswith('_test'): pytest.fail('Database name must end _test')
    url=url or 'sqlite:///'+str(tmp_path/'portal.db')
    engine=build_engine(url)
    if engine.dialect.name=='postgresql':
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name!='alembic_version'"))==0,'Test DB must be empty'
    cfg=Config(str(ROOT/'alembic.ini'));cfg.attributes['database_url']=url
    command.upgrade(cfg,'head');command.upgrade(cfg,'head')
    factory=session_factory(engine);seed(factory,password,password)
    settings=Settings(_env_file=None,database_url=url,app_secret=secrets.token_hex(32),proxy_secret=secrets.token_hex(32),internal_check_token=secrets.token_hex(32),test_context_secret=secrets.token_hex(32),log_file=str(tmp_path/'events.jsonl'))
    reader=InMemoryMetricReader();app=create_app(settings,engine,[reader])
    try:
        with TestClient(app) as client: yield client,factory,settings,reader
    finally:
        command.downgrade(cfg,'base');engine.dispose()
@pytest.fixture
def client(setup): return setup[0]
def login(client,password,username='test_user'):
    csrf=client.get('/api/auth/context').json()['csrf_token']
    return client.post('/api/auth/login',json={'username':username,'password':password},headers={'X-CSRF-Token':csrf})
