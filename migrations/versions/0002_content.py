"""Portal notices and board. Existing accounts and employee rows are preserved."""
from alembic import op
from portal.content import Notice, Post
revision='0002_content'
down_revision='0001_business_schema'
branch_labels=None
depends_on=None
def upgrade():
    Notice.__table__.create(op.get_bind())
    Post.__table__.create(op.get_bind())
def downgrade():
    Post.__table__.drop(op.get_bind())
    Notice.__table__.drop(op.get_bind())
