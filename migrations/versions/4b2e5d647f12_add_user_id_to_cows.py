"""add user_id to cows

Revision ID: 4b2e5d647f12
Revises: 3d16b30bb860
Create Date: 2026-05-11 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4b2e5d647f12'
down_revision = '3d16b30bb860'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('cows', schema=None) as batch_op:
        batch_op.add_column(sa.Column('user_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_cows_user_id_users',
            'users',
            ['user_id'],
            ['id']
        )


def downgrade():
    with op.batch_alter_table('cows', schema=None) as batch_op:
        batch_op.drop_constraint('fk_cows_user_id_users', type_='foreignkey')
        batch_op.drop_column('user_id')
