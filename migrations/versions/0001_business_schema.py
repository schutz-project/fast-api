"""Immutable initial business schema; no monitoring or raw event tables."""
from alembic import op
import sqlalchemy as sa
revision = '0001_business_schema'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('employees',
        sa.Column('employee_id', sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column('employee_code', sa.String(10), nullable=False, unique=True),
        sa.Column('name', sa.String(50), nullable=False),
        sa.Column('department', sa.String(50), nullable=False),
        sa.Column('position', sa.String(30), nullable=False),
        sa.Column('email', sa.String(100), nullable=False, unique=True),
        sa.Column('extension', sa.String(10)),
        sa.Column('employment_status', sa.String(20), nullable=False, server_default='active'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("employment_status IN ('active','inactive')", name='ck_employee_status'),
        *[sa.CheckConstraint(f'length(trim({c})) BETWEEN 1 AND {n}', name=f'ck_employee_{c}') for c,n in [('name',50),('employee_code',10),('department',50),('position',30),('email',100)]])
    op.create_index('ix_employee_name_status', 'employees', ['name','employment_status'])
    op.create_table('user_accounts',
        sa.Column('user_id', sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column('employee_id', sa.Integer(), sa.ForeignKey('employees.employee_id', ondelete='RESTRICT'), nullable=False, unique=True),
        sa.Column('username', sa.String(50), nullable=False, unique=True),
        sa.Column('password_hash', sa.Text(), nullable=False),
        sa.Column('role', sa.String(20), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('failed_attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_login_at', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("role IN ('admin','user')", name='ck_user_role'),
        sa.CheckConstraint('failed_attempts >= 0', name='ck_user_failed_attempts'),
        sa.CheckConstraint('length(trim(username)) BETWEEN 1 AND 50', name='ck_username'))
    op.create_table('sessions',
        sa.Column('session_id', sa.Uuid(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user_accounts.user_id', ondelete='RESTRICT'), nullable=False),
        sa.Column('session_token_hash', sa.String(64), nullable=False, unique=True),
        sa.Column('csrf_token_hash', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True)),
        sa.CheckConstraint('expires_at > created_at', name='ck_session_expiry'))
    op.create_index('ix_sessions_user_id','sessions',['user_id'])
    op.create_index('ix_sessions_expires_at','sessions',['expires_at'])

def downgrade():
    op.drop_table('sessions')
    op.drop_table('user_accounts')
    op.drop_table('employees')
