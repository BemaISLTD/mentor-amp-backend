"""Golden-case contract for FIA verification.

Expected values are intentionally supplied by the independent actuarial owner, not generated
from production functions.
"""

PRODUCT_CODE = "FIA"
ACTUARIAL_VERIFICATION_STATUS = "PENDING_OWNER_APPROVAL"

CASES: tuple[dict, ...] = ()