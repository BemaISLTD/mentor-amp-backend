"""Harden model version governance lifecycle, fingerprinting, and RBAC permissions.

Revision ID: e5b2c9a1d408
Revises: d4f7a9c2e681
"""

import uuid
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5b2c9a1d408"
down_revision: Union[str, Sequence[str], None] = "d4f7a9c2e681"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _actor_column(name: str) -> sa.Column:
    return sa.Column(name, sa.String(36), nullable=True)


def _actor_fk(table: str, column: str) -> None:
    op.create_foreign_key(
        f"fk_{table}_{column}_users", table, "users", [column], ["id"], ondelete="SET NULL"
    )


def upgrade() -> None:
    # 1. Add governance lifecycle and provenance columns to model_versions
    for col in ("validated_by", "approved_by", "rejected_by", "last_material_modified_by"):
        op.add_column("model_versions", _actor_column(col))
        _actor_fk("model_versions", col)

    op.add_column("model_versions", sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("model_versions", sa.Column("validation_fingerprint", sa.String(128), nullable=True))
    op.add_column("model_versions", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("model_versions", sa.Column("approved_fingerprint", sa.String(128), nullable=True))
    op.add_column("model_versions", sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("model_versions", sa.Column("rejection_reason", sa.Text(), nullable=True))
    op.add_column("model_versions", sa.Column("last_material_modified_at", sa.DateTime(timezone=True), nullable=True))

    # 2. Add partial unique index guaranteeing at most one current active version per model
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.create_index(
            "uq_model_versions_one_current",
            "model_versions",
            ["model_id"],
            unique=True,
            postgresql_where=sa.text("is_current = true AND deleted_at IS NULL"),
        )
    elif bind.dialect.name == "sqlite":
        op.create_index(
            "uq_model_versions_one_current",
            "model_versions",
            ["model_id"],
            unique=True,
            sqlite_where=sa.text("is_current = 1 AND deleted_at IS NULL"),
        )
    else:
        op.create_index(
            "uq_model_versions_one_current",
            "model_versions",
            ["model_id"],
            unique=True,
        )

    # 3. Seed dedicated models:approve permission and attach to admin, actuary, reviewer roles
    metadata = sa.MetaData()
    roles_table = sa.Table(
        "roles", metadata,
        sa.Column("id", sa.String(36)),
        sa.Column("name", sa.String(100)),
    )
    permissions_table = sa.Table(
        "permissions", metadata,
        sa.Column("id", sa.String(36)),
        sa.Column("name", sa.String(100)),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    role_permissions_table = sa.Table(
        "role_permissions", metadata,
        sa.Column("role_id", sa.String(36)),
        sa.Column("permission_id", sa.String(36)),
    )

    perm_id = bind.execute(
        sa.select(permissions_table.c.id).where(permissions_table.c.name == "models:approve")
    ).scalar_one_or_none()

    if not perm_id:
        perm_id = str(uuid.uuid4())
        bind.execute(
            permissions_table.insert().values(
                id=perm_id,
                name="models:approve",
                description="Approve or reject actuarial model versions.",
                created_at=datetime.now(timezone.utc),
            )
        )

    for role_name in ("admin", "actuary", "reviewer"):
        role_id = bind.execute(
            sa.select(roles_table.c.id).where(roles_table.c.name == role_name)
        ).scalar_one_or_none()
        if role_id:
            exists = bind.execute(
                sa.select(role_permissions_table.c.role_id).where(
                    role_permissions_table.c.role_id == role_id,
                    role_permissions_table.c.permission_id == perm_id,
                )
            ).scalar_one_or_none()
            if not exists:
                bind.execute(
                    role_permissions_table.insert().values(
                        role_id=role_id,
                        permission_id=perm_id,
                    )
                )


def downgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    permissions_table = sa.Table(
        "permissions", metadata,
        sa.Column("id", sa.String(36)),
        sa.Column("name", sa.String(100)),
    )
    role_permissions_table = sa.Table(
        "role_permissions", metadata,
        sa.Column("role_id", sa.String(36)),
        sa.Column("permission_id", sa.String(36)),
    )

    perm_id = bind.execute(
        sa.select(permissions_table.c.id).where(permissions_table.c.name == "models:approve")
    ).scalar_one_or_none()

    if perm_id:
        bind.execute(
            role_permissions_table.delete().where(role_permissions_table.c.permission_id == perm_id)
        )
        bind.execute(
            permissions_table.delete().where(permissions_table.c.id == perm_id)
        )

    op.drop_index("uq_model_versions_one_current", table_name="model_versions")

    for col in ("last_material_modified_by", "rejected_by", "approved_by", "validated_by"):
        op.drop_constraint(f"fk_model_versions_{col}_users", "model_versions", type_="foreignkey")

    for col in (
        "last_material_modified_at",
        "last_material_modified_by",
        "rejection_reason",
        "rejected_at",
        "rejected_by",
        "approved_fingerprint",
        "approved_at",
        "approved_by",
        "validation_fingerprint",
        "validated_at",
        "validated_by",
    ):
        op.drop_column("model_versions", col)
