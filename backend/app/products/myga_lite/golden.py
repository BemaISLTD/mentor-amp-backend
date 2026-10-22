"""Frozen results from the governed 25-policy, 480-month MYGA workflow.

These figures are MentorAmp internal deterministic regression anchors for continuous integration
and reproducibility verification. They represent internal deterministic regression targets for
the 25-policy portfolio, not an external LifeLib validation across the 25-policy set. External
LifeLib comparison is performed exclusively on the single anchor model point.
"""

GOLDEN_RESULTS: dict[str, float] = {
    "base_reserve": 4_163_674.901809879,
    "rates_up_reserve": 4_201_201.546339422,
    "base_undiscounted_outgo": 5_298_179.05844914,
    "rates_up_undiscounted_outgo": 5_358_149.664428924,
    "base_policy_0001_month_30_surrender_benefit": 51_979.599258014285,
}
