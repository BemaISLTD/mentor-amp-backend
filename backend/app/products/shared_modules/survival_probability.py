"""Survival probability calculations."""


def survival_probability(
    base_age: int,
    gender: str,
    duration: int,
    mortality_table: list[dict],
) -> float:
    """Calculate the probability of surviving from base_age to base_age + duration.

    Args:
        base_age: Starting age in years
        gender: 'M' or 'F'
        duration: Number of years to survive
        mortality_table: List of dicts with mortality_rate by age/gender

    Returns:
        Probability (0.0 to 1.0) of surviving the full duration
    """
    if duration <= 0:
        return 1.0

    prob = 1.0
    for year_offset in range(duration):
        current_age = base_age + year_offset
        rate = _get_rate(current_age, gender, year_offset, mortality_table)
        prob *= (1.0 - rate)

    return max(prob, 0.0)


def _get_rate(age: int, gender: str, duration: int, table: list[dict]) -> float:
    """Get mortality rate for a specific age/gender/duration from the table."""
    for row in table:
        row_age = row.get("age")
        row_gender = row.get("gender")
        row_duration = row.get("duration")

        age_match = row_age is not None and int(row_age) == int(age)
        gender_match = row_gender is None or str(row_gender).upper() == str(gender).upper()
        duration_match = row_duration is None or int(row_duration) == int(duration)

        if age_match and gender_match and duration_match:
            return float(row.get("mortality_rate", 0))

    if table:
        return float(table[0].get("mortality_rate", 0))
    return 0.0
