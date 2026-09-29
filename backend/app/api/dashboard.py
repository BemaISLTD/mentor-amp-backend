"""Project-level dashboard aggregations."""

from collections import defaultdict
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models.inforce import InforceFile
from app.db.models.product import AssetPosition, Product
from app.db.models.project import Project
from app.db.models.run import Run
from app.models.dashboard import (
    DashboardStatsResponse,
    NamedCount,
    NamedValue,
    ProductValue,
    RecentRun,
)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/stats", response_model=DashboardStatsResponse)
def get_dashboard_stats(
    project_id: str = Query(...),
    as_of_date: date | None = Query(None),
    db: Session = Depends(get_db),
):
    if db.query(Project).filter(Project.id == project_id).first() is None:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found.")

    products = db.query(Product).filter(
        Product.project_id == project_id,
        Product.deleted_at.is_(None),
    ).all()
    products_by_type: dict[str, int] = defaultdict(int)
    products_by_status: dict[str, int] = defaultdict(int)
    for product in products:
        products_by_type[product.product_type] += 1
        products_by_status[product.status] += 1

    selected_date = as_of_date
    if selected_date is None:
        selected_date = db.query(func.max(AssetPosition.as_of_date)).filter(
            AssetPosition.project_id == project_id,
            AssetPosition.deleted_at.is_(None),
        ).scalar()

    positions = []
    if selected_date is not None:
        positions = db.query(AssetPosition).filter(
            AssetPosition.project_id == project_id,
            AssetPosition.as_of_date == selected_date,
            AssetPosition.deleted_at.is_(None),
        ).all()

    assets_by_class: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    currency_totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    values_by_product: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    total_market_value = Decimal("0")
    for position in positions:
        value = Decimal(position.market_value)
        total_market_value += value
        assets_by_class[position.asset_class] += value
        currency_totals[position.currency] += value
        values_by_product[position.product_id] += value

    products_by_id = {product.id: product for product in products}
    product_mix = [
        ProductValue(
            product_id=product_id,
            code=products_by_id[product_id].code,
            name=products_by_id[product_id].name,
            product_type=products_by_id[product_id].product_type,
            market_value=value,
        )
        for product_id, value in values_by_product.items()
        if product_id in products_by_id
    ]
    product_mix.sort(key=lambda item: (-item.market_value, item.code))

    runs = db.query(Run).filter(Run.project_id == project_id).all()
    runs_by_status: dict[str, int] = defaultdict(int)
    for run in runs:
        runs_by_status[run.status] += 1
    recent_runs = sorted(runs, key=lambda run: run.created_at, reverse=True)[:5]

    inforce_files = db.query(InforceFile).filter(
        InforceFile.project_id == project_id
    ).all()

    return DashboardStatsResponse(
        project_id=project_id,
        product_count=len(products),
        active_product_count=products_by_status.get("active", 0),
        products_by_type=[
            NamedCount(name=name, count=count)
            for name, count in sorted(products_by_type.items())
        ],
        products_by_status=[
            NamedCount(name=name, count=count)
            for name, count in sorted(products_by_status.items())
        ],
        asset_as_of_date=selected_date,
        total_market_value=total_market_value,
        assets_by_class=[
            NamedValue(name=name, value=value)
            for name, value in sorted(assets_by_class.items())
        ],
        product_mix=product_mix,
        currency_totals=[
            NamedValue(name=name, value=value)
            for name, value in sorted(currency_totals.items())
        ],
        run_count=len(runs),
        runs_by_status=[
            NamedCount(name=name, count=count)
            for name, count in sorted(runs_by_status.items())
        ],
        recent_runs=[RecentRun.model_validate(run, from_attributes=True) for run in recent_runs],
        inforce_file_count=len(inforce_files),
        policy_record_count=sum(item.row_count for item in inforce_files),
    )
