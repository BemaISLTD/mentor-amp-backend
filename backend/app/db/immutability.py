"""ORM guard for write-once records (run packages, final run manifests).

The PostgreSQL migration adds a trigger with the same rule, so the database refuses updates
even from code that bypasses the ORM.
"""

from sqlalchemy import event, inspect


class ImmutableRecordError(Exception):
    """Raised when code tries to modify a record that must never change after it is written."""


def forbid_updates(model: type) -> None:
    @event.listens_for(model, "before_update")
    def _refuse(mapper, connection, target) -> None:  # noqa: ARG001 - SQLAlchemy signature
        state = inspect(target)
        changed = [
            attr.key for attr in state.attrs
            if attr.key in mapper.column_attrs.keys() and attr.history.has_changes()
        ]
        if changed:
            raise ImmutableRecordError(
                f"{model.__name__} records are immutable; attempted to change {changed}."
            )
