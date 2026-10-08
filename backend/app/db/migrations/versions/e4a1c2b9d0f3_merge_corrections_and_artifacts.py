"""Join correction-pass and Parquet artifact migration histories.

Revision ID: e4a1c2b9d0f3
Revises: c9e2a4b6d8f1, c6e1a4b9d203
"""

from typing import Sequence, Union

revision: str = "e4a1c2b9d0f3"
down_revision: Union[str, Sequence[str], None] = ("c9e2a4b6d8f1", "c6e1a4b9d203")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
