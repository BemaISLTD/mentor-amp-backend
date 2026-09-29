"""Product catalog, mapping, and asset-position API contracts."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ProductCreate(BaseModel):
    project_id: str
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    product_type: str = Field(min_length=1, max_length=100)
    status: Literal["draft", "active", "inactive"] = "active"
    configuration: dict[str, Any] = Field(default_factory=dict)

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return value.strip().upper()


class ProductUpdate(BaseModel):
    code: str | None = Field(None, min_length=1, max_length=100)
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    product_type: str | None = Field(None, min_length=1, max_length=100)
    status: Literal["draft", "active", "inactive"] | None = None
    configuration: dict[str, Any] | None = None

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str | None) -> str | None:
        return value.strip().upper() if value is not None else None


class ProductResponse(BaseModel):
    id: str
    project_id: str
    code: str
    name: str
    description: str | None = None
    product_type: str
    status: str
    configuration: dict[str, Any]
    created_by: str | None = None
    updated_by: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProductListResponse(BaseModel):
    products: list[ProductResponse] = Field(default_factory=list)
    total: int
    limit: int
    offset: int


class ProductMappingCreate(BaseModel):
    source_system: str = Field(min_length=1, max_length=100)
    source_product_code: str = Field(min_length=1, max_length=255)
    effective_from: date
    effective_to: date | None = None
    mapping_data: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_date_range(self):
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to cannot be before effective_from.")
        return self


class ProductMappingResponse(ProductMappingCreate):
    id: str
    product_id: str
    created_by: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AssetPositionCreate(BaseModel):
    product_id: str
    as_of_date: date
    asset_class: str = Field(min_length=1, max_length=100)
    security_id: str | None = Field(None, max_length=255)
    market_value: Decimal
    currency: str = Field("USD", min_length=3, max_length=3)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()


class AssetPositionUpdate(BaseModel):
    product_id: str | None = None
    as_of_date: date | None = None
    asset_class: str | None = Field(None, min_length=1, max_length=100)
    security_id: str | None = Field(None, max_length=255)
    market_value: Decimal | None = None
    currency: str | None = Field(None, min_length=3, max_length=3)
    attributes: dict[str, Any] | None = None

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str | None) -> str | None:
        return value.upper() if value is not None else None


class AssetPositionResponse(BaseModel):
    id: str
    project_id: str
    product_id: str
    as_of_date: date
    asset_class: str
    security_id: str | None = None
    market_value: Decimal
    currency: str
    attributes: dict[str, Any]
    created_by: str | None = None
    updated_by: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AssetPositionListResponse(BaseModel):
    positions: list[AssetPositionResponse] = Field(default_factory=list)
    total: int
    limit: int
    offset: int
