"""The run log shown in the execution monitor."""

from typing import Any

from sqlalchemy.orm import Session

from app.db.models.projection import RunEvent


def add_event(
    db: Session, run_id: str, step: str, message: str, level: str = "info", data: Any = None
) -> None:
    db.add(RunEvent(run_id=run_id, step=step, message=message, level=level, data=data))
