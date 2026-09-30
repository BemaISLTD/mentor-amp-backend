"""Expand built-in RBAC roles and read permissions.

Revision ID: d1a4c7e9b205
Revises: c48a2d7159be
Create Date: 2026-09-30
"""

import uuid
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d1a4c7e9b205"
down_revision: Union[str, None] = "c48a2d7159be"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ROLE_PERMISSIONS = {
    "admin": {
        "projects:read", "projects:write", "registries:read", "registries:write",
        "imports:read", "imports:write", "runs:read", "runs:execute",
    },
    "actuary": {
        "projects:read", "registries:read", "registries:write", "imports:read",
        "imports:write", "runs:read", "runs:execute",
    },
    "model_developer": {
        "projects:read", "registries:read", "registries:write", "imports:read",
        "imports:write", "runs:read", "runs:execute",
    },
    "reviewer": {"projects:read", "registries:read", "imports:read", "runs:read"},
    "read_only": {"projects:read", "registries:read", "imports:read", "runs:read"},
}

ROLE_DESCRIPTIONS = {
    "admin": "Full platform access.",
    "actuary": "Actuarial data, model, and execution access.",
    "model_developer": "Model configuration, data import, and test execution access.",
    "reviewer": "Read-only review access to models, data, runs, and results.",
    "read_only": "Read-only access to projects and published actuarial information.",
}

PERMISSIONS = {
    "projects:read": "View projects.",
    "projects:write": "Create and modify projects.",
    "registries:read": "View actuarial registries.",
    "registries:write": "Modify actuarial registries.",
    "imports:read": "View imported actuarial data.",
    "imports:write": "Upload actuarial data files.",
    "runs:read": "View projection runs and results.",
    "runs:execute": "Start projection runs.",
}

NEW_PERMISSION_NAMES = ["imports:read", "runs:read"]


def _tables():
    metadata = sa.MetaData()
    roles = sa.Table(
        "roles", metadata,
        sa.Column("id", sa.String(36)),
        sa.Column("name", sa.String(100)),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    permissions = sa.Table(
        "permissions", metadata,
        sa.Column("id", sa.String(36)),
        sa.Column("name", sa.String(100)),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    role_permissions = sa.Table(
        "role_permissions", metadata,
        sa.Column("role_id", sa.String(36)),
        sa.Column("permission_id", sa.String(36)),
    )
    return roles, permissions, role_permissions


def upgrade() -> None:
    bind = op.get_bind()
    roles, permissions, role_permissions = _tables()

    permission_ids = {
        name: permission_id
        for name, permission_id in bind.execute(
            sa.select(permissions.c.name, permissions.c.id)
        ).tuples()
    }
    for name, description in PERMISSIONS.items():
        if name not in permission_ids:
            permission_id = str(uuid.uuid4())
            bind.execute(
                permissions.insert().values(
                    id=permission_id,
                    name=name,
                    description=description,
                    created_at=datetime.now(timezone.utc),
                )
            )
            permission_ids[name] = permission_id

    role_ids = {
        name: role_id
        for name, role_id in bind.execute(sa.select(roles.c.name, roles.c.id)).tuples()
    }
    for name, description in ROLE_DESCRIPTIONS.items():
        if name not in role_ids:
            role_id = str(uuid.uuid4())
            bind.execute(
                roles.insert().values(
                    id=role_id,
                    name=name,
                    description=description,
                    created_at=datetime.now(timezone.utc),
                )
            )
            role_ids[name] = role_id

    for role_name, permission_names in ROLE_PERMISSIONS.items():
        role_id = role_ids.get(role_name)
        if role_id is None:
            continue
        bind.execute(
            role_permissions.delete().where(role_permissions.c.role_id == role_id)
        )
        bind.execute(
            role_permissions.insert(),
            [
                {"role_id": role_id, "permission_id": permission_ids[name]}
                for name in sorted(permission_names)
            ],
        )


def downgrade() -> None:
    bind = op.get_bind()
    roles, permissions, _ = _tables()
    bind.execute(
        roles.delete().where(
            roles.c.name.in_(["model_developer", "reviewer", "read_only"])
        )
    )
    bind.execute(
        permissions.delete().where(permissions.c.name.in_(NEW_PERMISSION_NAMES))
    )
