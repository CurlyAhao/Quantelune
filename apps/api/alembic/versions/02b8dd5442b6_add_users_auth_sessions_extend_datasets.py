"""add_users_auth_sessions_extend_datasets

Revision ID: 02b8dd5442b6
Revises: 5a1bbd15fea4
Create Date: 2026-07-23 12:32:18.098024

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '02b8dd5442b6'
down_revision: Union[str, Sequence[str], None] = '5a1bbd15fea4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Pre-create the enum type so ALTER TABLE can reference it
uploadstatus = sa.Enum(
    'PENDING', 'UPLOADING', 'UPLOADED', 'VALIDATING',
    'READY', 'FAILED', 'DELETED', name='uploadstatus',
)


def upgrade() -> None:
    """Upgrade schema."""
    # Create enum type first
    uploadstatus.create(op.get_bind(), checkfirst=True)

    op.create_table('users',
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('display_name', sa.String(length=255), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)

    op.create_table('auth_sessions',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked', sa.Boolean(), nullable=False),
    sa.Column('user_agent', sa.String(length=512), nullable=True),
    sa.Column('ip_address', sa.String(length=45), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_auth_sessions_token_hash'), 'auth_sessions', ['token_hash'], unique=True)
    op.create_index(op.f('ix_auth_sessions_user_id'), 'auth_sessions', ['user_id'], unique=False)

    # Extend datasets — use server_default for NOT NULL on existing table
    op.add_column('datasets', sa.Column('original_filename', sa.String(length=512), nullable=False, server_default=''))
    op.add_column('datasets', sa.Column('safe_filename', sa.String(length=512), nullable=False, server_default=''))
    op.add_column('datasets', sa.Column('file_format', sa.String(length=20), nullable=False, server_default=''))
    op.add_column('datasets', sa.Column('mime_type', sa.String(length=127), nullable=False, server_default=''))
    op.add_column('datasets', sa.Column('file_size', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('datasets', sa.Column('sha256', sa.String(length=64), nullable=False, server_default=''))
    op.add_column('datasets', sa.Column('object_key', sa.String(length=1024), nullable=False, server_default=''))
    op.add_column('datasets', sa.Column('bucket', sa.String(length=255), nullable=False, server_default='statforge'))
    op.add_column('datasets', sa.Column('preview_json', sa.Text(), nullable=True))
    op.add_column('datasets', sa.Column('upload_status', uploadstatus, nullable=False, server_default='PENDING'))
    op.add_column('datasets', sa.Column('error_code', sa.String(length=100), nullable=True))
    op.add_column('datasets', sa.Column('error_message', sa.Text(), nullable=True))
    op.add_column('datasets', sa.Column('validated_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f('ix_datasets_sha256'), 'datasets', ['sha256'], unique=False)

    # Extend projects
    op.add_column('projects', sa.Column('owner_id', sa.Uuid(), nullable=True))
    op.add_column('projects', sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.create_index(op.f('ix_projects_owner_id'), 'projects', ['owner_id'], unique=False)
    op.create_foreign_key(None, 'projects', 'users', ['owner_id'], ['id'], ondelete='CASCADE')

    # Extend variables
    op.add_column('variables', sa.Column('nullable', sa.Boolean(), nullable=True))
    op.add_column('variables', sa.Column('sample_values_json', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('variables', 'sample_values_json')
    op.drop_column('variables', 'nullable')
    op.drop_constraint(None, 'projects', type_='foreignkey')
    op.drop_index(op.f('ix_projects_owner_id'), table_name='projects')
    op.drop_column('projects', 'is_deleted')
    op.drop_column('projects', 'owner_id')
    op.drop_index(op.f('ix_datasets_sha256'), table_name='datasets')
    op.drop_column('datasets', 'validated_at')
    op.drop_column('datasets', 'error_message')
    op.drop_column('datasets', 'error_code')
    op.drop_column('datasets', 'upload_status')
    op.drop_column('datasets', 'preview_json')
    op.drop_column('datasets', 'bucket')
    op.drop_column('datasets', 'object_key')
    op.drop_column('datasets', 'sha256')
    op.drop_column('datasets', 'file_size')
    op.drop_column('datasets', 'mime_type')
    op.drop_column('datasets', 'file_format')
    op.drop_column('datasets', 'safe_filename')
    op.drop_column('datasets', 'original_filename')
    op.drop_index(op.f('ix_auth_sessions_user_id'), table_name='auth_sessions')
    op.drop_index(op.f('ix_auth_sessions_token_hash'), table_name='auth_sessions')
    op.drop_table('auth_sessions')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_table('users')

    # Drop enum type
    uploadstatus.drop(op.get_bind(), checkfirst=True)
