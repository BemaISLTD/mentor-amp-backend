"""Registers every product's formula functions with the formula engine.

Called once at application startup (``app.main``) and again by the run service before a run
executes, so background workers and scripts always see the functions. Registration is
idempotent.
"""

from app.core.formula_engine.formulas import FORMULA_FUNCTIONS

REGISTERED_PRODUCTS: list[str] = []


def register_all_products() -> list[str]:
    """Register all product formula functions; return the product codes registered."""
    from app.products.spia_lite import config as spia
    from app.products.spia_reference import config as spia_reference
    from app.products.myga_lite import config as myga

    spia.register_functions()
    spia_reference.register_functions()
    myga.register_functions()
    if spia.PRODUCT_CODE not in REGISTERED_PRODUCTS:
        REGISTERED_PRODUCTS.append(spia.PRODUCT_CODE)
    if myga.PRODUCT_CODE not in REGISTERED_PRODUCTS:
        REGISTERED_PRODUCTS.append(myga.PRODUCT_CODE)
    return list(REGISTERED_PRODUCTS)


def registered_function_count() -> int:
    return len(FORMULA_FUNCTIONS)
