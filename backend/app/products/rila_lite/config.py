"""RILA illustrative vertical, executed by the shared projection chain.

All reference-contract decisions remain pending owner approval (ACT-M4-DEC-01..15).
"""

from app.products.shared_modules.indexed_model import definition, register

PRODUCT_CODE = "RILA"
PRODUCT_NAME = "RILA illustrative indexed annuity"
IMPLEMENTATION_VERSION = "indexed-illustrative-m4-v2"
MODEL, VARIABLES, FORMULAS, PUBLISHED_OUTPUTS, _FUNCTIONS = definition(PRODUCT_CODE)
MODEL_DEFINITION = {"model": MODEL, "variables": VARIABLES, "formulas": FORMULAS,
                    "published_outputs": PUBLISHED_OUTPUTS}


def register_functions() -> None:
    register(PRODUCT_CODE, _FUNCTIONS)