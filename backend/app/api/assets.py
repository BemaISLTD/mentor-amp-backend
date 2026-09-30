"""Dated asset positions associated with products."""

from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, require_permissions
from app.api.products import _get_product_or_404
from app.core.audit import record_audit
from app.db.database import get_db
from app.db.models.product import AssetPosition
from app.db.models.user import User
from app.models.products import (
    AssetPositionCreate,
    AssetPositionListResponse,
    AssetPositionResponse,
    AssetPositionUpdate,
)

router = APIRouter(prefix="/asset-positions", tags=["assets"])


def _get_position_or_404(position_id: str, db: Session) -> AssetPosition:
    position = db.query(AssetPosition).filter(
        AssetPosition.id == position_id,
        AssetPosition.deleted_at.is_(None),
    ).first()
    if position is None:
        raise HTTPException(status_code=404, detail=f"Asset position '{position_id}' not found.")
    return position


def _position_state(position: AssetPosition) -> dict:
    return {
        "project_id": position.project_id,
        "product_id": position.product_id,
        "as_of_date": position.as_of_date.isoformat(),
        "asset_class": position.asset_class,
        "security_id": position.security_id,
        "market_value": str(position.market_value),
        "currency": position.currency,
        "attributes": position.attributes,
    }


@router.post(
    "/",
    response_model=AssetPositionResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("registries:write"))],
)
def create_position(
    payload: AssetPositionCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    product = _get_product_or_404(payload.product_id, db)
    position = AssetPosition(
        project_id=product.project_id,
        **payload.model_dump(),
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    db.add(position)
    db.flush()
    record_audit(
        db,
        actor_user_id=current_user.id,
        action="asset_position.created",
        entity_type="asset_position",
        entity_id=position.id,
        after_state=_position_state(position),
    )
    db.commit()
    db.refresh(position)
    return position


@router.get("/", response_model=AssetPositionListResponse)
def list_positions(
    project_id: str | None = Query(None),
    product_id: str | None = Query(None),
    as_of_date: date | None = Query(None),
    asset_class: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    query = db.query(AssetPosition).filter(AssetPosition.deleted_at.is_(None))
    if project_id is not None:
        query = query.filter(AssetPosition.project_id == project_id)
    if product_id is not None:
        query = query.filter(AssetPosition.product_id == product_id)
    if as_of_date is not None:
        query = query.filter(AssetPosition.as_of_date == as_of_date)
    if asset_class is not None:
        query = query.filter(AssetPosition.asset_class == asset_class)
    total = query.count()
    positions = (
        query.order_by(AssetPosition.as_of_date.desc(), AssetPosition.id)
        .offset(offset)
        .limit(limit)
        .all()
    )
    return AssetPositionListResponse(
        positions=positions, total=total, limit=limit, offset=offset
    )


@router.get("/{position_id}", response_model=AssetPositionResponse)
def get_position(position_id: str, db: Session = Depends(get_db)):
    return _get_position_or_404(position_id, db)


@router.patch(
    "/{position_id}",
    response_model=AssetPositionResponse,
    dependencies=[Depends(require_permissions("registries:write"))],
)
def update_position(
    position_id: str,
    payload: AssetPositionUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    position = _get_position_or_404(position_id, db)
    before_state = _position_state(position)
    updates = payload.model_dump(exclude_unset=True)
    if updates.get("product_id") is not None:
        product = _get_product_or_404(updates["product_id"], db)
        position.project_id = product.project_id
    for field, value in updates.items():
        setattr(position, field, value)
    position.updated_by = current_user.id
    db.flush()
    record_audit(
        db,
        actor_user_id=current_user.id,
        action="asset_position.updated",
        entity_type="asset_position",
        entity_id=position.id,
        before_state=before_state,
        after_state=_position_state(position),
    )
    db.commit()
    db.refresh(position)
    return position


@router.delete(
    "/{position_id}",
    status_code=204,
    dependencies=[Depends(require_permissions("registries:write"))],
)
def delete_position(
    position_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> Response:
    position = _get_position_or_404(position_id, db)
    before_state = _position_state(position)
    position.deleted_at = datetime.now(timezone.utc)
    position.updated_by = current_user.id
    record_audit(
        db,
        actor_user_id=current_user.id,
        action="asset_position.deleted",
        entity_type="asset_position",
        entity_id=position.id,
        before_state=before_state,
    )
    db.commit()
    return Response(status_code=204)
