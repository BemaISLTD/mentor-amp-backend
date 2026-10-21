"""Frozen results from the governed 25-policy, 480-month MYGA workflow.

These figures were accepted only after the external LifeLib cross-check passed for every
month in both scenarios at a maximum absolute difference below one millionth of a dollar.
"""

GOLDEN_RESULTS: dict[str, float] = {
    "base_reserve": 4_163_674.901809879,
    "rates_up_reserve": 4_201_201.546339422,
    "base_undiscounted_outgo": 5_298_179.05844914,
    "rates_up_undiscounted_outgo": 5_358_149.664428924,
    "base_policy_0001_month_30_surrender_benefit": 51_979.599258014285,
}
