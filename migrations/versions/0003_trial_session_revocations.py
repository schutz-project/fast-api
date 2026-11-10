"""Add trial session revocation receipts; never undo revoked sessions."""
from alembic import op
from portal.session_control import SessionRevocation
revision = '0003_trial_session_revocations'
down_revision = '0002_content'
branch_labels = None
depends_on = None

def upgrade():
    SessionRevocation.__table__.create(op.get_bind())

def downgrade():
    from sqlalchemy import select, func
    if op.get_bind().scalar(select(func.count()).select_from(SessionRevocation)):
        raise RuntimeError('Nonempty revocation receipts must be retained; use forward-only rollback.')
    SessionRevocation.__table__.drop(op.get_bind())
