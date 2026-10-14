"""Join package and artifact-storage migration histories.

Revision ID: f8c2d4e6a901
Revises: a7f4c2d9e180, b7e4d2a9c613
"""

from typing import Sequence, Union

revision: str = "f8c2d4e6a901"
down_revision: Union[str, Sequence[str], None] = ("a7f4c2d9e180", "b7e4d2a9c613")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
