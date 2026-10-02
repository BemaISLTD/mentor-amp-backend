"""Governed product catalog and source-system mappings."""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, require_permissions
from app.core.audit import record_audit
from app.core.project_lifecycle import require_active_project
from app.db.database import get_db
from app.db.models.product import AssetPosition, Product, ProductMapping
from app.db.models.user import User
from app.models.products import (
    ProductCreate,
    ProductListResponse,
    ProductMappingCreate,
    ProductMappingResponse,
    ProductResponse,
    ProductUpdate,
)
from app.services import access

router = APIRouter(prefix="/products", tags=["products"])


def _get_product_or_404(product_id: str, db: Session) -> Product:
    product = (
        db.query(Product)
        .filter(Product.id == product_id, Product.deleted_at.is_(None))
        .first()
    )
    if product is None:
        raise HTTPException(status_code=404, detail=f"Product '{product_id}' not found.")
    return product


def _product_state(product: Product) -> dict:
    return {
        "project_id": product.project_id,
        "code": product.code,
        "name": product.name,
        "product_type": product.product_type,
        "status": product.status,
        "configuration": product.configuration,
    }


def _ensure_unique_code(
    db: Session, project_id: str, code: str, exclude_id: str | None = None
) -> None:
    query = db.query(Product).filter(
        Product.project_id == project_id,
        Product.code == code,
        Product.deleted_at.is_(None),
    )
    if exclude_id is not None:
        query = query.filter(Product.id != exclude_id)
    if query.first() is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Product code '{code}' already exists in this project.",
        )


@router.post(
    "/",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("registries:write"))],
)
def create_product(
    payload: ProductCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    access.require_project_access(db, current_user, payload.project_id, write=True)
    require_active_project(db, payload.project_id)
    _ensure_unique_code(db, payload.project_id, payload.code)
    product = Product(
        **payload.model_dump(),
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    db.add(product)
    db.flush()
    record_audit(
        db,
        actor_user_id=current_user.id,
        action="product.created",
        entity_type="product",
        entity_id=product.id,
        after_state=_product_state(product),
    )
    db.commit()
    db.refresh(product)
    return product


@router.get("/", response_model=ProductListResponse)
def list_products(
    user: Annotated[User, Depends(get_current_user)],
    project_id: str | None = Query(None),
    product_type: str | None = Query(None),
    product_status: str | None = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    query = db.query(Product).filter(Product.deleted_at.is_(None))
    if project_id is not None:
        access.require_project_access(db, user, project_id)
        query = query.filter(Product.project_id == project_id)
    else:
        allowed = access.accessible_project_ids(db, user)
        if allowed is not None:
            query = query.filter(Product.project_id.in_(allowed or [""]))
    if product_type is not None:
        query = query.filter(Product.product_type == product_type)
    if product_status is not None:
        query = query.filter(Product.status == product_status)
    total = query.count()
    products = query.order_by(Product.code).offset(offset).limit(limit).all()
    return ProductListResponse(
        products=products, total=total, limit=limit, offset=offset
    )


@router.get("/{product_id}", response_model=ProductResponse)
def get_product(
    product_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    access.require_object_access(db, user, "product", product_id)
    return _get_product_or_404(product_id, db)


@router.patch(
    "/{product_id}",
    response_model=ProductResponse,
    dependencies=[Depends(require_permissions("registries:write"))],
)
def update_product(
    product_id: str,
    payload: ProductUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    product = _get_product_or_404(product_id, db)
    access.require_project_access(db, current_user, product.project_id, write=True)
    require_active_project(db, product.project_id)
    updates = payload.model_dump(exclude_unset=True)
    if "code" in updates:
        _ensure_unique_code(db, product.project_id, updates["code"], exclude_id=product.id)
    before_state = _product_state(product)
    for field, value in updates.items():
        setattr(product, field, value)
    product.updated_by = current_user.id
    db.flush()
    record_audit(
        db,
        actor_user_id=current_user.id,
        action="product.updated",
        entity_type="product",
        entity_id=product.id,
        before_state=before_state,
        after_state=_product_state(product),
    )
    db.commit()
    db.refresh(product)
    return product


@router.delete(
    "/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permissions("registries:write"))],
)
def delete_product(
    product_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> Response:
    product = _get_product_or_404(product_id, db)
    access.require_project_access(db, current_user, product.project_id, write=True)
    require_active_project(db, product.project_id)
    deleted_at = datetime.now(timezone.utc)
    before_state = _product_state(product)
    product.deleted_at = deleted_at
    product.updated_by = current_user.id
    db.query(ProductMapping).filter(
        ProductMapping.product_id == product.id,
        ProductMapping.deleted_at.is_(None),
    ).update({ProductMapping.deleted_at: deleted_at}, synchronize_session=False)
    db.query(AssetPosition).filter(
        AssetPosition.product_id == product.id,
        AssetPosition.deleted_at.is_(None),
    ).update(
        {
            AssetPosition.deleted_at: deleted_at,
            AssetPosition.updated_by: current_user.id,
        },
        synchronize_session=False,
    )
    record_audit(
        db,
        actor_user_id=current_user.id,
        action="product.deleted",
        entity_type="product",
        entity_id=product.id,
        before_state=before_state,
    )
    db.commit()
    return Response(status_code=204)


@router.post(
    "/{product_id}/mappings",
    response_model=ProductMappingResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permissions("registries:write"))],
)
def create_mapping(
    product_id: str,
    payload: ProductMappingCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    product = _get_product_or_404(product_id, db)
    access.require_project_access(db, current_user, product.project_id, write=True)
    require_active_project(db, product.project_id)
    duplicate = db.query(ProductMapping).filter(
        ProductMapping.product_id == product_id,
        ProductMapping.source_system == payload.source_system,
        ProductMapping.source_product_code == payload.source_product_code,
        ProductMapping.effective_from == payload.effective_from,
        ProductMapping.deleted_at.is_(None),
    ).first()
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="This product mapping already exists.")
    mapping = ProductMapping(
        product_id=product_id,
        **payload.model_dump(),
        created_by=current_user.id,
    )
    db.add(mapping)
    db.flush()
    record_audit(
        db,
        actor_user_id=current_user.id,
        action="product_mapping.created",
        entity_type="product_mapping",
        entity_id=mapping.id,
        after_state={
            "product_id": product_id,
            "source_system": mapping.source_system,
            "source_product_code": mapping.source_product_code,
            "effective_from": mapping.effective_from.isoformat(),
            "effective_to": mapping.effective_to.isoformat() if mapping.effective_to else None,
        },
    )
    db.commit()
    db.refresh(mapping)
    return mapping


@router.get("/{product_id}/mappings", response_model=list[ProductMappingResponse])
def list_mappings(
    product_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    access.require_object_access(db, user, "product", product_id)
    _get_product_or_404(product_id, db)
    return db.query(ProductMapping).filter(
        ProductMapping.product_id == product_id,
        ProductMapping.deleted_at.is_(None),
    ).order_by(ProductMapping.effective_from.desc()).all()


@router.delete(
    "/{product_id}/mappings/{mapping_id}",
    status_code=204,
    dependencies=[Depends(require_permissions("registries:write"))],
)
def delete_mapping(
    product_id: str,
    mapping_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
) -> Response:
    product = _get_product_or_404(product_id, db)
    access.require_project_access(db, current_user, product.project_id, write=True)
    require_active_project(db, product.project_id)
    mapping = db.query(ProductMapping).filter(
        ProductMapping.id == mapping_id,
        ProductMapping.product_id == product_id,
        ProductMapping.deleted_at.is_(None),
    ).first()
    if mapping is None:
        raise HTTPException(status_code=404, detail=f"Mapping '{mapping_id}' not found.")
    mapping.deleted_at = datetime.now(timezone.utc)
    record_audit(
        db,
        actor_user_id=current_user.id,
        action="product_mapping.deleted",
        entity_type="product_mapping",
        entity_id=mapping.id,
        before_state={
            "product_id": product_id,
            "source_system": mapping.source_system,
            "source_product_code": mapping.source_product_code,
        },
    )
    db.commit()
    return Response(status_code=204)
