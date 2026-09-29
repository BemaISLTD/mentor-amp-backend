"""Project dashboard aggregation contracts."""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class NamedCount(BaseModel):
    name: str
    count: int


class NamedValue(BaseModel):
    name: str
    value: Decimal


class ProductValue(BaseModel):
    product_id: str
    code: str
    name: str
    product_type: str
    market_value: Decimal


class RecentRun(BaseModel):
    id: str
    status: str
    created_at: datetime
    completed_at: datetime | None = None


class DashboardStatsResponse(BaseModel):
    project_id: str
    product_count: int
    active_product_count: int
    products_by_type: list[NamedCount] = Field(default_factory=list)
    products_by_status: list[NamedCount] = Field(default_factory=list)
    asset_as_of_date: date | None = None
    total_market_value: Decimal = Decimal("0")
    assets_by_class: list[NamedValue] = Field(default_factory=list)
    product_mix: list[ProductValue] = Field(default_factory=list)
    currency_totals: list[NamedValue] = Field(default_factory=list)
    run_count: int
    runs_by_status: list[NamedCount] = Field(default_factory=list)
    recent_runs: list[RecentRun] = Field(default_factory=list)
    inforce_file_count: int
    policy_record_count: int
