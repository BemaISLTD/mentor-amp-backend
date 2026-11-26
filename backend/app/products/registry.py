"""Registers every product's formula functions with the formula engine.

Called once at application startup (``app.main``) and again by the run service before a run
executes, so background workers and scripts always see the functions. Registration is
idempotent.
"""

from app.core.formula_engine.formulas import FORMULA_FUNCTIONS
from app.products.inforce_registry import (
    COMMON_INFORCE_FIELDS,
    ProductInforceDefinition,
    clear_product_inforce_registry,
    get_product_inforce_definition,
    get_registered_inforce_products,
    register_inforce_product,
)

# Authoritative implemented Launch 1.0 actuarial product families (7-product launch scope:
# SPIA, MYGA, FIA, RILA, IDI, VA, PRT). Internal GPU/benchmark reference models are tracked
# separately in REGISTERED_INTERNAL_REFERENCES so they are never counted as an eighth product.
REGISTERED_PRODUCTS: list[str] = []
REGISTERED_INTERNAL_REFERENCES: list[str] = []


def register_all_products() -> list[str]:
    """Register all actuarial product and internal reference formula functions; return Launch product codes."""
    from app.products.spia_lite import config as spia
    from app.products.spia_reference import config as spia_reference
    from app.products.myga_lite import config as myga
    from app.products.fia_lite import config as fia
    from app.products.rila_lite import config as rila
    from app.products.prospective_reserve_discounting import config as reserve_gpu
    from app.products.idi_lite import config as idi
    from app.products.va_lite import config as va
    from app.products.prt_lite import config as prt

    spia.register_functions()
    spia_reference.register_functions()
    myga.register_functions()
    fia.register_functions()
    rila.register_functions()
    reserve_gpu.register_functions()
    idi.register_functions()
    va.register_functions()
    prt.register_functions()
    if spia.PRODUCT_CODE not in REGISTERED_PRODUCTS:
        REGISTERED_PRODUCTS.append(spia.PRODUCT_CODE)
    if myga.PRODUCT_CODE not in REGISTERED_PRODUCTS:
        REGISTERED_PRODUCTS.append(myga.PRODUCT_CODE)
    for product in (fia, rila, idi, va, prt):
        if product.PRODUCT_CODE not in REGISTERED_PRODUCTS:
            REGISTERED_PRODUCTS.append(product.PRODUCT_CODE)
    if reserve_gpu.PRODUCT_CODE not in REGISTERED_INTERNAL_REFERENCES:
        REGISTERED_INTERNAL_REFERENCES.append(reserve_gpu.PRODUCT_CODE)
    return list(REGISTERED_PRODUCTS)


def registered_internal_references() -> list[str]:
    """Return registered illustrative internal numerical reference codes (non-launch products)."""
    return list(REGISTERED_INTERNAL_REFERENCES)


def registered_function_count() -> int:
    return len(FORMULA_FUNCTIONS)
