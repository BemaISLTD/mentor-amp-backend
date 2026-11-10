"""Assumptions & Actuarial Factor Table Engine REST APIs.

Provides endpoints for factor tables, multi-dimensional grid lookups,
Scale G2 / MP mortality projections, annual-to-monthly decrements,
yield/spot curve calculations, and dynamic policyholder behavioral lapse schedules.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from app.api.dependencies import get_current_user
from app.db.models.user import User
from app.services import assumptions_service

router = APIRouter(tags=["assumptions"])


# =============================================================================
# Request & Response Schemas
# =============================================================================

class TableLookupRequest(BaseModel):
    coordinates: dict[str, Any] = Field(..., description="Map of dimension names to coordinate values.")
    interpolation: str | None = Field(None, description="Optional interpolation override: STEP_FLOOR, STEP_NEAREST, LINEAR, BILINEAR, MULTILINEAR.")
    boundary_policy: str | None = Field(None, description="Optional boundary policy override: FAIL_CLOSED, CLAMP_TO_BOUNDS, EXTRAPOLATE_FLAT.")


class MortalityProjectionRequest(BaseModel):
    table_id: str = Field(..., description="Base mortality table ID.")
    attained_age: int = Field(..., ge=0, le=150, description="Attained age at base year.")
    sex: str = Field(..., description="Sex token, e.g. 'M' or 'F'.")
    base_year: int = Field(..., ge=1900, le=2200, description="Base valuation calendar year.")
    projection_year: int = Field(..., ge=1900, le=2200, description="Target projection calendar year.")
    improvement_table_id: str | None = Field(None, description="Optional improvement table (e.g. Scale G2 or MP matrix).")
    base_qx: float | None = Field(None, ge=0.0, le=1.0, description="Optional base mortality qx override.")
    mode: str = Field("FIXED_ATTAINED_AGE", description="'FIXED_ATTAINED_AGE' or 'GENERATIONAL'.")
    terminal_age: int | None = Field(None, ge=1, le=150, description="Optional terminal age omega ceiling.")


class MonthlyDecrementRequest(BaseModel):
    annual_qx: float = Field(..., ge=0.0, le=1.0, description="Annual mortality decrement rate qx.")
    method: str = Field("constant_force", description="Decrement methodology: 'constant_force' or 'udd'.")
    month_index: int = Field(1, ge=1, le=12, description="1-based month index in year (used for UDD).")


class DiscountFactorRequest(BaseModel):
    tenor_years: float = Field(..., ge=0.0, description="Requested tenor in years.")


class BaselineLapseRequest(BaseModel):
    duration: int = Field(..., ge=1, description="Policy duration in years.")
    early_duration_rates: list[float] | None = Field(None, description="Optional surrender-period annual lapse rates.")
    expiration_shock_rate: float | None = Field(None, ge=0.0, le=1.0, description="Optional lapse rate at surrender expiry.")
    post_shock_rate: float | None = Field(None, ge=0.0, le=1.0, description="Optional post-shock baseline lapse rate.")
    ultimate_rate: float | None = Field(None, ge=0.0, le=1.0, description="Optional ultimate baseline lapse rate.")
    grading_years: int = Field(3, ge=1, description="Grading period from post-shock to ultimate rate.")


class DynamicLapseRequest(BaseModel):
    base_lapse: float = Field(..., ge=0.0, le=1.0, description="Baseline policyholder lapse rate.")
    market_rate: float = Field(..., description="Current competitor market interest or crediting rate.")
    contract_rate: float = Field(..., description="Current policy contract crediting rate.")
    buffer: float = Field(0.01, ge=0.0, description="Interest rate buffer before dynamic lapse triggers.")
    threshold: float = Field(0.02, gt=0.0, description="Spread scaling threshold.")
    alpha: float = Field(2.0, ge=0.0, description="Dynamic multiplier scale sensitivity.")
    beta: float = Field(1.0, gt=0.0, description="Spread power exponent.")
    max_lapse_rate: float = Field(0.50, ge=0.0, le=1.0, description="Absolute ceiling for effective lapse rate.")


# =============================================================================
# Endpoints
# =============================================================================

@router.get("/assumptions/tables")
def list_factor_tables(
    user: Annotated[User, Depends(get_current_user)],
):
    del user
    return {"tables": assumptions_service.list_tables()}


@router.get("/projects/{project_id}/assumptions/tables")
def list_project_factor_tables(
    project_id: str,
    user: Annotated[User, Depends(get_current_user)],
):
    del user
    return {"tables": assumptions_service.list_tables(project_id)}


@router.get("/assumptions/tables/{table_id}")
def get_factor_table(
    table_id: str,
    user: Annotated[User, Depends(get_current_user)],
):
    del user
    return assumptions_service.get_table_detail(table_id)


@router.post("/assumptions/tables/{table_id}/lookup")
def lookup_table_value(
    table_id: str,
    payload: TableLookupRequest,
    user: Annotated[User, Depends(get_current_user)],
):
    del user
    return assumptions_service.lookup_table_value(
        table_id=table_id,
        coordinates=payload.coordinates,
        interpolation=payload.interpolation,
        boundary_policy=payload.boundary_policy,
    )


@router.get("/assumptions/mortality/tables")
def list_mortality_tables(
    user: Annotated[User, Depends(get_current_user)],
):
    del user
    return {"mortality_tables": assumptions_service.list_mortality_tables()}


@router.post("/assumptions/mortality/project")
def project_mortality(
    payload: MortalityProjectionRequest,
    user: Annotated[User, Depends(get_current_user)],
):
    del user
    return assumptions_service.project_mortality_rate(
        table_id=payload.table_id,
        attained_age=payload.attained_age,
        sex=payload.sex,
        base_year=payload.base_year,
        projection_year=payload.projection_year,
        improvement_table_id=payload.improvement_table_id,
        base_qx=payload.base_qx,
        mode=payload.mode,
        terminal_age=payload.terminal_age,
    )


@router.post("/assumptions/mortality/monthly-decrement")
def compute_monthly_decrement(
    payload: MonthlyDecrementRequest,
    user: Annotated[User, Depends(get_current_user)],
):
    del user
    return assumptions_service.compute_monthly_decrement(
        annual_qx=payload.annual_qx,
        method=payload.method,
        month_index=payload.month_index,
    )


@router.get("/assumptions/curves")
def list_yield_curves(
    user: Annotated[User, Depends(get_current_user)],
):
    del user
    return {"curves": assumptions_service.list_yield_curves()}


@router.get("/assumptions/curves/{curve_id}")
def get_curve_detail(
    curve_id: str,
    user: Annotated[User, Depends(get_current_user)],
):
    del user
    return assumptions_service.get_curve_detail(curve_id)


@router.post("/assumptions/curves/{curve_id}/discount-factor")
def compute_discount_factor(
    curve_id: str,
    payload: DiscountFactorRequest,
    user: Annotated[User, Depends(get_current_user)],
):
    del user
    return assumptions_service.compute_discount_factor(
        curve_id=curve_id,
        tenor_years=payload.tenor_years,
    )


@router.post("/assumptions/behavior/baseline-lapse")
def evaluate_baseline_lapse(
    payload: BaselineLapseRequest,
    user: Annotated[User, Depends(get_current_user)],
):
    del user
    return assumptions_service.compute_baseline_lapse(
        duration=payload.duration,
        early_duration_rates=payload.early_duration_rates,
        expiration_shock_rate=payload.expiration_shock_rate,
        post_shock_rate=payload.post_shock_rate,
        ultimate_rate=payload.ultimate_rate,
        grading_years=payload.grading_years,
    )


@router.post("/assumptions/behavior/evaluate")
def evaluate_dynamic_lapse(
    payload: DynamicLapseRequest,
    user: Annotated[User, Depends(get_current_user)],
):
    del user
    return assumptions_service.compute_effective_lapse(
        base_lapse=payload.base_lapse,
        market_rate=payload.market_rate,
        contract_rate=payload.contract_rate,
        buffer=payload.buffer,
        threshold=payload.threshold,
        alpha=payload.alpha,
        beta=payload.beta,
        max_lapse_rate=payload.max_lapse_rate,
    )
